#!/bin/bash
# verify.sh — このハーネス自身が、宣伝どおりに動くことを機械で証明する。
#
# 「買う前の 3 つの問い」のうち「成果を外から確かめられるか」に、製品自身が答える。
# 買った人が最初に叩くコマンドであり、CI で毎回走るコマンドでもある。
#
#   ./verify.sh          人間可読
#   ./verify.sh --json   機械可読（1 行 JSON）
#
# exit 0 = 全検査合格 / exit 1 = 違反あり / exit 2 = 検査自体が成立しなかった
#
# 設計上の約束（docs/harness/principles.md P2・docs/harness/incidents.md A-2「静かなゼロ」）:
#   - すべてのカウントは **分母つき** で出す
#   - 走査対象 0 件は正常系にしない（❗ で異常扱いし exit 2）
#   - skip を成功として数えない
set -uo pipefail

# 本リポでは scripts/ 配下に置くので、1 つ上がリポジトリルート
HOME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HOME_DIR" || exit 2

JSON=0
[ "${1:-}" = "--json" ] && JSON=1

pass=0
fail=0
FINDINGS=()

note()  { [ "$JSON" -eq 0 ] && printf '%s\n' "$*"; return 0; }
ok()    { pass=$((pass + 1)); [ "$JSON" -eq 0 ] && printf '  ✅ %s\n' "$*"; return 0; }
bad()   { fail=$((fail + 1)); FINDINGS+=("$1"); [ "$JSON" -eq 0 ] && printf '  ❌ %s\n' "$1"; return 0; }
fatal() { [ "$JSON" -eq 0 ] && printf '❗ %s\n' "$*" || printf '{"status":"inconclusive","reason":"%s"}\n' "$*"; exit 2; }

note "# proof-harness verify — $(date '+%Y-%m-%d %H:%M:%S %Z')"
note ""

# ---------------------------------------------------------------------------
# 0. 前提: 走査対象が存在すること（分母ゼロを正常系にしない）
# ---------------------------------------------------------------------------
# ⚠️ **export 先では「自分が出した分」だけを検査する**（2026-09-10）。
#    公開先には受け手自身の skill / script が既に在り、glob で拾うと
#    **受け手のファイルを勝手に検査して落とす**（実際 kit の pulse-precheck.sh を
#    「self-test 未実装」と落とし、35 本を回してタイムアウトした）。
#    manifest が在ればそれを分母にし、無ければ正本の layout を見る。
MANIFEST="$HOME_DIR/docs/harness/manifest.txt"
SKILL_FILES=()
ENGINE_FILES=()
if [ -f "$MANIFEST" ]; then
  while IFS=$'\t' read -r _kind _path; do
    case "$_kind" in
      skill)  [ -f "$HOME_DIR/$_path" ] && SKILL_FILES+=("$_path") ;;
      engine) [ -f "$HOME_DIR/$_path" ] && ENGINE_FILES+=("$_path") ;;
    esac
  done < "$MANIFEST"
  [ "${#SKILL_FILES[@]}" -gt 0 ] || fatal "manifest の skill が 0 件 — 検査が成立しない（分母ゼロ）"
  [ "${#ENGINE_FILES[@]}" -gt 0 ] || fatal "manifest の engine が 0 件 — 検査が成立しない（分母ゼロ）"
else
  SKILL_FILES=(skills/*/SKILL.md)
  [ -e "${SKILL_FILES[0]}" ] || fatal "skills/*/SKILL.md が 0 件 — 検査が成立しない（分母ゼロ）"
  while IFS= read -r f; do ENGINE_FILES+=("$f"); done < <(
    find scripts -maxdepth 1 -type f \( -name '*.py' -o -name '*.sh' \) ! -name '_*' | sort
  )
  [ "${#ENGINE_FILES[@]}" -gt 0 ] || fatal "scripts にエンジンが 0 件 — 検査が成立しない（分母ゼロ）"
fi
N_SKILLS=${#SKILL_FILES[@]}
N_ENGINES=${#ENGINE_FILES[@]}

command -v python3 >/dev/null 2>&1 || fatal "python3 が見つからない（未導入か PATH 不達）— skip せず異常として停止"

note "走査対象: skill ${N_SKILLS} 本 / engine ${N_ENGINES} 本"
note ""

# ---------------------------------------------------------------------------
# 1. 各エンジンの --self-test が exit 0 を返す
#    自己申告ではなく終了コードで判定する（P1/P2）
# ---------------------------------------------------------------------------
note "## 1. エンジン self-test（分母 ${N_ENGINES}）"
st_have=0
for e in "${ENGINE_FILES[@]}"; do
  if ! grep -q -- "--self-test" "$e" 2>/dev/null; then
    bad "self-test 未実装: $e（skip は成功ではない）"
    continue
  fi
  st_have=$((st_have + 1))
  case "$e" in
    *.py) python3 "$e" --self-test >/dev/null 2>&1 ;;
    *)    bash    "$e" --self-test >/dev/null 2>&1 ;;
  esac
  rc=$?
  if [ "$rc" -eq 0 ]; then ok "$(basename "$e")"; else bad "self-test 失敗 (exit $rc): $e"; fi
done
note "  → self-test 実装 ${st_have}/${N_ENGINES} 本"
note ""

# ---------------------------------------------------------------------------
# 2. SKILL.md が参照するハーネス内パスが実在する（dangling 0）
#    「買ったが中のリンクが全部死んでいる」を機械で禁じる
# ---------------------------------------------------------------------------
note "## 2. 内部参照の死活（分母 ${N_SKILLS} 本の SKILL.md）"
dangling=$(python3 - "$HOME_DIR" "${SKILL_FILES[@]}" <<'PY'
import os, re, sys
home = sys.argv[1]
pat = re.compile(r'`(scripts/[^`]+|docs/harness/[^`]+|skills/[^`]+)`')
bad = []
n_refs = 0
# ⚠️ ディレクトリを再走査しない（2026-09-10）。export 先では layout が違い、
#    `skills` は存在しない。**呼び出し側が解決した SKILL_FILES をそのまま受ける**。
for rel in sys.argv[2:]:
    d = os.path.basename(os.path.dirname(rel))
    p = os.path.join(home, rel)
    if not os.path.isfile(p):
        continue
    txt = open(p, encoding="utf-8").read()
    for m in pat.finditer(txt):
        ref = m.group(1).split()[0].rstrip(".,)、。")
        # glob / placeholder は「命名パターン」であって実在すべきパスではない
        if "*" in ref or "<" in ref or "{" in ref:
            continue
        n_refs += 1
        if not os.path.exists(os.path.join(home, ref)):
            bad.append(f"{d}: {ref}")
print(n_refs)
for b in bad:
    print("DANGLING " + b)
PY
)
n_refs=$(printf '%s\n' "$dangling" | head -1)
n_dangle=$(printf '%s\n' "$dangling" | grep -c '^DANGLING ' || true)
if [ "${n_refs:-0}" -eq 0 ]; then
  bad "SKILL.md 内のハーネス内参照が 0 件 — 走査できていない疑い（分母ゼロ）"
else
  if [ "$n_dangle" -eq 0 ]; then
    ok "参照 ${n_refs} 件すべて実在（dangling 0）"
  else
    printf '%s\n' "$dangling" | grep '^DANGLING ' | while IFS= read -r l; do
      [ "$JSON" -eq 0 ] && printf '     %s\n' "$l"
    done
    bad "dangling 参照 ${n_dangle}/${n_refs} 件"
  fi
fi
note ""

# ---------------------------------------------------------------------------
# 3. 配布物に配布元固有の識別子が残っていない
#    ホームパス・個人名・社内ドメインが残っていると購入者環境で動かない
# ---------------------------------------------------------------------------
note "## 3. 配布元固有識別子の残存（分母 skill ${N_SKILLS} + engine ${N_ENGINES}）"
leak_total=0
# __pycache__ は .gitignore 済で配布物に含まれない（.pyc は生成時の絶対パスを持つ）。
# 「配布されるもの」を検査対象にするため除外する。
# ⚠️ **検査する語を配布物に埋め込まない**（2026-09-09）。
#    埋め込むと、配布物を読んだ人に**こちらの内部の名前空間が一覧で見える**。
#    「配布元固有識別子」は配る側ごとに違うので、設定から読む。
#    VERIFY_LEAK_PATTERNS  空白区切りの ERE。未設定なら汎用の既定を使う。
#    配る側は自分のホームパス・個人名・社内ドメインをここに入れる。
# ⚠️ **ERE で書く**。grep は既定が BRE なので -E を付けないと `+` や `{}` が
#    リテラルになり、**1 件も当たらないまま 0 件と報告する**（2026-09-09 に実際に踏んだ）。
#    linuxbrew のような既知のシステムパスは個人のホームではないので除く。
_DEFAULT_LEAK_PATS='/home/[a-z0-9_-]+/ /Users/[A-Za-z0-9_-]+/ [A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
for pat in ${VERIFY_LEAK_PATTERNS:-$_DEFAULT_LEAK_PATS}; do
  c=$(grep -rlE --exclude-dir=__pycache__ --exclude='*.pyc' "$pat" "${SKILL_FILES[@]}" "${ENGINE_FILES[@]}" 2>/dev/null | wc -l | tr -d ' ')
  leak_total=$((leak_total + c))
  if [ "$c" -gt 0 ]; then
    bad "配布元固有識別子 '$pat' が ${c} ファイルに残存"
    grep -rlE --exclude-dir=__pycache__ --exclude='*.pyc' "$pat" "${SKILL_FILES[@]}" "${ENGINE_FILES[@]}" 2>/dev/null \
      | while IFS= read -r l; do [ "$JSON" -eq 0 ] && printf '     %s\n' "$l"; done
  fi
done
[ "$leak_total" -eq 0 ] && ok "配布元固有識別子 0 件（$(printf '%s\n' ${VERIFY_LEAK_PATTERNS:-$_DEFAULT_LEAK_PATS} | wc -w | tr -d ' ') パターン × skill ${N_SKILLS} + engine ${N_ENGINES}）"
note ""

# ---------------------------------------------------------------------------
# 4. 全 skill に frontmatter (name / description) がある
#    Claude Code はこれが無いと skill を認識しない
# ---------------------------------------------------------------------------
note "## 4. frontmatter 充足（分母 ${N_SKILLS}）"
fm_ok=0
for f in "${SKILL_FILES[@]}"; do
  if head -1 "$f" | grep -q '^---$' && grep -q '^name:' "$f" && grep -q '^description:' "$f"; then
    fm_ok=$((fm_ok + 1))
  else
    bad "frontmatter 不備: $f"
  fi
done
[ "$fm_ok" -eq "$N_SKILLS" ] && ok "frontmatter ${fm_ok}/${N_SKILLS} 本"
note ""

# ---------------------------------------------------------------------------
# 5. エンジンが外部ネットワークに依存していない（オフラインで動く）
# ---------------------------------------------------------------------------
note "## 5. ネットワーク非依存（分母 ${N_ENGINES}）"
net=$(grep -l 'urllib\.request\|requests\.\(get\|post\)\|http\.client\|curl -\|wget ' "${ENGINE_FILES[@]}" 2>/dev/null | wc -l | tr -d ' ')
if [ "$net" -eq 0 ]; then
  ok "ネットワーク呼び出し 0 件（${N_ENGINES} 本走査）"
else
  grep -l 'urllib\.request\|requests\.\(get\|post\)\|http\.client\|curl -\|wget ' "${ENGINE_FILES[@]}" 2>/dev/null \
    | while IFS= read -r l; do [ "$JSON" -eq 0 ] && printf '     %s\n' "$l"; done
  bad "ネットワーク依存 ${net}/${N_ENGINES} 本（オフライン実行の保証が崩れる）"
fi
note ""

# ---------------------------------------------------------------------------
# 6. 配布セットに実行時生成物が混入していない
#    エージェント実行時ログ・バイトコード・状態ファイルは、作業中に自動生成され、
#    黙って追跡対象に入る。ツール呼び出し履歴や絶対パスを含むため配布してはいけない。
#    （初回コミット時に .claude/logs/*.jsonl が実際に混入した）
# ---------------------------------------------------------------------------
note "## 6. 実行時生成物の混入（git 追跡対象を検査）"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  tracked=$(git ls-files | wc -l | tr -d ' ')
  if [ "$tracked" -eq 0 ]; then
    note "  （未コミット — 追跡対象 0 件。commit 後に再実行してください）"
  else
    dirty=$(git ls-files | grep -cE '(^|/)\.claude/logs/|\.pyc$|__pycache__/|\.goal-loop-state$|\.DS_Store$' || true)
    if [ "$dirty" -eq 0 ]; then
      ok "実行時生成物 0 件（追跡 ${tracked} ファイル走査）"
    else
      git ls-files | grep -E '(^|/)\.claude/logs/|\.pyc$|__pycache__/|\.goal-loop-state$|\.DS_Store$' \
        | while IFS= read -r l; do [ "$JSON" -eq 0 ] && printf '     %s\n' "$l"; done
      bad "実行時生成物 ${dirty}/${tracked} ファイルが追跡対象に混入"
    fi
  fi
else
  note "  （git リポジトリ外 — 検査対象なし）"
fi
note ""

# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
total=$((pass + fail))
if [ "$total" -eq 0 ]; then
  fatal "検査が 1 件も実行されなかった"
fi

if [ "$JSON" -eq 1 ]; then
  printf '{"status":"%s","checks":%d,"pass":%d,"fail":%d,"skills":%d,"engines":%d,"findings":[' \
    "$([ "$fail" -eq 0 ] && echo pass || echo fail)" "$total" "$pass" "$fail" "$N_SKILLS" "$N_ENGINES"
  sep=""
  for f in "${FINDINGS[@]:-}"; do
    [ -z "$f" ] && continue
    printf '%s"%s"' "$sep" "$(printf '%s' "$f" | sed 's/"/\\"/g')"
    sep=","
  done
  printf ']}\n'
else
  printf '\n───────────────────────────────\n'
  printf '検査 %d 件: 合格 %d / 不合格 %d\n' "$total" "$pass" "$fail"
  if [ "$fail" -eq 0 ]; then
    printf '✅ PASS — skill %d 本 / engine %d 本が宣伝どおり動作\n' "$N_SKILLS" "$N_ENGINES"
  else
    printf '❌ FAIL — 上記 %d 件を解消するまで出荷しない\n' "$fail"
  fi
fi

[ "$fail" -eq 0 ] && exit 0 || exit 1
