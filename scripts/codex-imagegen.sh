#!/bin/bash
# codex-imagegen.sh — Codex CLI の image_gen ツールで画像を生成し、実ファイルを検品する。
#
# 設計方針:
#   - 生成（非決定的・LLM）と 検品（決定論・スクリプト）を分離する。
#   - exit 0 は「画像が出来た」ではなく「実ファイルが画像として妥当だった」を意味する（fail-closed）。
#   - プレビューではなく **保存された実ファイル**のマジックバイトと実寸を検査する
#     （2026-07-31 の Gemini 事例: プレビューと DL 版が乖離していた）。
#
# 使い方:
#   scripts/codex-imagegen.sh --prompt "..." --out path/to/out.png [options]
#
# Options:
#   --prompt <text>     生成指示（必須）
#   --out <path>        出力ファイルパス（必須・.png / .jpg）
#   --aspect <spec>     "1:1" / "16:9" / "4:5" など。プロンプトに添えるだけで厳密保証はしない
#   --ref <file>        参照画像（繰り返し可・codex exec -i に渡す）
#   --attempts <n>      検品失敗時の再試行回数（既定 2）
#   --min-bytes <n>     最小バイト数（既定 10000・空/破損検知）
#   --model <name>      codex のモデル（既定 gpt-5.6-sol）
#   --effort <level>    reasoning effort（既定 low — 画像生成品質は reasoning では上がらない）
#   --timeout <sec>     1 回あたりの上限秒（既定 300）
#
# Exit codes:
#   0 = 生成 + 検品 pass
#   1 = 検品 fail（試行を使い切った）
#   2 = 使い方の誤り / codex 不在

set -uo pipefail

PROMPT=""
OUT=""
ASPECT=""
ATTEMPTS=2
MIN_BYTES=10000
MODEL="gpt-5.6-sol"
EFFORT="low"
TIMEOUT_SEC=300
REFS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --prompt)     PROMPT="${2:-}"; shift 2 ;;
    --out)        OUT="${2:-}"; shift 2 ;;
    --aspect)     ASPECT="${2:-}"; shift 2 ;;
    --ref)        REFS+=("${2:-}"); shift 2 ;;
    --attempts)   ATTEMPTS="${2:-}"; shift 2 ;;
    --min-bytes)  MIN_BYTES="${2:-}"; shift 2 ;;
    --model)      MODEL="${2:-}"; shift 2 ;;
    --effort)     EFFORT="${2:-}"; shift 2 ;;
    --timeout)    TIMEOUT_SEC="${2:-}"; shift 2 ;;
    -h|--help)    sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "❌ 不明な引数: $1" >&2; exit 2 ;;
  esac
done

[ -n "$PROMPT" ] || { echo "❌ --prompt は必須" >&2; exit 2; }
[ -n "$OUT" ]    || { echo "❌ --out は必須" >&2; exit 2; }
command -v codex >/dev/null 2>&1 || { echo "❌ codex CLI が見つからない（codex-imagegen は Codex CLI に依存する）" >&2; exit 2; }

OUT_DIR="$(cd "$(dirname "$OUT")" 2>/dev/null && pwd)" || {
  mkdir -p "$(dirname "$OUT")" && OUT_DIR="$(cd "$(dirname "$OUT")" && pwd)"
}
OUT_ABS="$OUT_DIR/$(basename "$OUT")"

# ---- 検品（決定論・LLM を使わない）-------------------------------------------
# 実ファイルのみを見る。codex の自己申告は根拠にしない（ADR-019）。
verify_image() {
  local f="$1"
  [ -f "$f" ] || { echo "  検品: ❌ ファイルが存在しない ($f)"; return 1; }

  local bytes
  bytes=$(wc -c < "$f" | tr -d ' ')
  if [ "$bytes" -lt "$MIN_BYTES" ]; then
    echo "  検品: ❌ サイズ過小 ${bytes}B < ${MIN_BYTES}B（空/切断の疑い）"
    return 1
  fi

  python3 - "$f" <<'PY'
import struct, sys
p = sys.argv[1]
d = open(p, 'rb').read()
if d[:8] == b'\x89PNG\r\n\x1a\n':
    # IHDR は必ず先頭チャンク
    if d[12:16] != b'IHDR':
        print("  検品: ❌ PNG だが IHDR が無い（破損）"); sys.exit(1)
    w, h = struct.unpack('>II', d[16:24])
    fmt = 'PNG'
elif d[:3] == b'\xff\xd8\xff':
    w = h = None
    i = 2
    while i < len(d) - 9:
        if d[i] != 0xFF:
            i += 1; continue
        m = d[i+1]
        if m in (0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF):
            h, w = struct.unpack('>HH', d[i+5:i+9]); break
        if m in (0xD8,0xD9) or 0xD0 <= m <= 0xD7:
            i += 2; continue
        seg = struct.unpack('>H', d[i+2:i+4])[0]
        i += 2 + seg
    if w is None:
        print("  検品: ❌ JPEG だが SOF が見つからない（破損）"); sys.exit(1)
    fmt = 'JPEG'
else:
    print(f"  検品: ❌ 画像フォーマット不明（先頭バイト {d[:4]!r}）"); sys.exit(1)

if w < 16 or h < 16:
    print(f"  検品: ❌ 実寸が異常に小さい {w}x{h}"); sys.exit(1)

print(f"  検品: ✅ {fmt} {w}x{h} / {len(d)} bytes")
PY
}

# ---- 生成 -------------------------------------------------------------------
ASPECT_LINE=""
[ -n "$ASPECT" ] && ASPECT_LINE="アスペクト比は ${ASPECT} を狙ってください。"

REF_ARGS=()
for r in "${REFS[@]:-}"; do
  [ -n "$r" ] && REF_ARGS+=(-i "$r")
done

attempt=1
while [ "$attempt" -le "$ATTEMPTS" ]; do
  echo "▶ codex-imagegen 試行 ${attempt}/${ATTEMPTS} → $OUT_ABS"
  rm -f "$OUT_ABS"

  REQ="image_gen ツールを使って次の画像を1枚生成し、生成された画像ファイルを必ず絶対パス '${OUT_ABS}' にコピーして保存してください。
${ASPECT_LINE}
保存後は、そのパスとバイトサイズだけを短く報告してください。画像の説明文や講評は不要です。
Python やベクタ描画で自前に画像を描くことは禁止です（必ず image_gen を使う）。

--- 生成する画像 ---
${PROMPT}"

  # codex が対象ディレクトリに書けるよう workspace-write で実行する。
  # 引数配列は空のとき展開しない（"${arr[@]:-}" は空文字を 1 個渡してしまい codex が usage エラーになる）。
  CMD=(codex exec --skip-git-repo-check --sandbox workspace-write
       -m "$MODEL" -c model_reasoning_effort="$EFFORT")
  [ ${#REF_ARGS[@]} -gt 0 ] && CMD+=("${REF_ARGS[@]}")
  CMD+=("$REQ")

  # macOS には timeout(1) が無い。gtimeout / timeout があれば使い、無ければ無防備で走らせず警告する。
  if command -v gtimeout >/dev/null 2>&1;  then CMD=(gtimeout "$TIMEOUT_SEC" "${CMD[@]}")
  elif command -v timeout >/dev/null 2>&1; then CMD=(timeout  "$TIMEOUT_SEC" "${CMD[@]}")
  else echo "  ⚠️ timeout(1) 不在のため上限秒を強制できない（brew install coreutils で gtimeout 導入可）"; fi

  ( cd "$OUT_DIR" && "${CMD[@]}" ) >/tmp/codex-imagegen-$$.log 2>&1
  rc=$?

  if [ $rc -ne 0 ]; then
    echo "  codex exec が非ゼロ終了 (rc=$rc)。末尾ログ:"
    tail -5 /tmp/codex-imagegen-$$.log | sed 's/^/    /'
  fi

  if verify_image "$OUT_ABS"; then
    rm -f /tmp/codex-imagegen-$$.log
    echo "✅ 生成 + 検品 pass: $OUT_ABS"
    echo "⚠️  対外公開する前に、必ず人間（または別エージェント）が実ファイルを目視すること。"
    echo "    検品が保証するのは『妥当な画像ファイルであること』だけで、内容の正しさではない。"
    exit 0
  fi

  attempt=$((attempt + 1))
done

echo "❌ ${ATTEMPTS} 回試行しても検品を通らなかった: $OUT_ABS"
echo "   codex ログ: /tmp/codex-imagegen-$$.log"
exit 1
