#!/bin/bash
# =============================================================
# pre-write-collision.sh — PreToolUse hook for Write
#
# 既存の追跡済みファイルへの Write を、同一セッションで一度も Read していない場合に
# 止める。Edit は「先に Read すること」が harness 側で強制されているのに、Write には
# その縛りが無く、**存在するファイルを丸ごと上書きできてしまう**非対称があるため。
#
# 実害の実例（2026-08-12）: 5 分前に別セッションが実装・push した受入テストを、
# 新規作成のつもりの Write が丸ごと上書きした。Write の応答は "created" ではなく
# "updated" だったが、誰も止めなかった。未 push だったため復元できたが、
# 「上書きの前に対象を見る」は運用の心がけでは守れない。
#
# 判定:
#   - 対象が存在しない            → 許可（新規作成）
#   - git 管理外                  → 許可（scratch / 生成物）
#   - 同一セッションで Read 済み  → 許可
#   - ツールログが存在しない      → 許可 + 警告（「読んでいない」と「計測できていない」を区別する）
#   - それ以外                    → ブロック（exit 2）
#
# Exit: 0 = allow, 2 = block（parse 不能は fail-closed）
# Portable: macOS BSD 互換（grep -P / \s を使わない）
# =============================================================
set -uo pipefail

INPUT=$(cat)

# --- パースは単一 CLI に依存させない（jq 欠如で全 Write を止めた前例がある） ---
parse_field() {
  field="$1"
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$INPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
cur = d
for k in '$field'.split('.'):
    if not isinstance(cur, dict):
        sys.exit(0)
    cur = cur.get(k)
print(cur if isinstance(cur, str) else '')
" 2>/dev/null
  elif command -v jq >/dev/null 2>&1; then
    printf '%s' "$INPUT" | jq -r ".${field} // empty" 2>/dev/null
  else
    echo "__NO_PARSER__"
  fi
}

FILE_PATH="$(parse_field 'tool_input.file_path')"
SESSION_ID="$(parse_field 'session_id')"

if [ "$FILE_PATH" = "__NO_PARSER__" ]; then
  echo "BLOCKED: pre-write-collision.sh requires python3 or jq (neither installed)." >&2
  echo "         Install one, or remove this hook from settings.json." >&2
  exit 2
fi

# file_path が取れないのは想定外の入力。security 系 Pre* は fail-closed。
if [ -z "$FILE_PATH" ]; then
  echo "BLOCKED: pre-write-collision.sh could not parse tool_input.file_path." >&2
  exit 2
fi

# 新規作成は素通し（上書きではない）
[ -e "$FILE_PATH" ] || exit 0

# git 管理外（scratch・ビルド生成物・ログ）は素通し
DIR="$(dirname "$FILE_PATH")"
if ! git -C "$DIR" ls-files --error-unmatch "$FILE_PATH" >/dev/null 2>&1; then
  exit 0
fi

# 検査対象のリポジトリ（= 利用者のプロジェクト）を解決する。
#
# ⚠️ plugin として配ると `$0` は **plugin のキャッシュ配下**になる（2026-09-10 実測）。
#    `dirname "$0"/../..` を「リポジトリのルート」として使うと、設定もログも
#    plugin の中を指す。設定は永久に見つからず、ログは全プロジェクトで共有される。
#    兄弟ファイル（`_advisory-log.sh` 等）を引くのに `$0` を使うのは正しい。
#    **プロジェクトを指したいときだけ**これを使う。
_project_dir() {
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then printf '%s' "$CLAUDE_PROJECT_DIR"; return; fi
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

HOOK_REPO="$(_project_dir)"
# テストが実ログを退避して壊さずに縮退パスを検査できるよう、差し替え口だけ開ける。
# 既定は従来どおりリポジトリ内のログ。
LOG_DIR="${CLAUDE_TOOL_LOG_DIR:-${HOOK_REPO}/.claude/logs}"
TODAY_LOG="${LOG_DIR}/$(date +%Y-%m-%d)-tools.jsonl"
# セッションが日付をまたぐことがあるので前日も見る
YEST_LOG="${LOG_DIR}/$(date -u -d '1 day ago' +%Y-%m-%d 2>/dev/null || date -v-1d +%Y-%m-%d 2>/dev/null)-tools.jsonl"

LOGS=()
[ -s "$TODAY_LOG" ] && LOGS+=("$TODAY_LOG")
[ -s "$YEST_LOG" ] && LOGS+=("$YEST_LOG")

if [ "${#LOGS[@]}" -eq 0 ]; then
  # 「Read していない」ではなく「観測できていない」。黙って通すと偽の緑になるので鳴らす。
  echo "NOTE: pre-write-collision.sh could not find a tool log; overwrite guard is NOT in effect." >&2
  echo "      (post-tool-log.sh が動いていない可能性がある)" >&2
  exit 0
fi

if command -v python3 >/dev/null 2>&1; then
  READ_FOUND=$(FILE="$FILE_PATH" SESSION="$SESSION_ID" python3 - "${LOGS[@]}" <<'PYEOF'
import json, os, sys
target = os.environ["FILE"]
session = os.environ["SESSION"]
for path in sys.argv[1:]:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("tool") != "Read" or r.get("file") != target:
                    continue
                # session が記録されていない古い行は、セッション一致を主張できないので採らない
                if session and r.get("session") == session:
                    print("yes")
                    sys.exit(0)
    except OSError:
        continue
print("no")
PYEOF
)
else
  # python3 が無い環境向けの縮退判定（行内に 3 条件がすべて現れるか）
  READ_FOUND="no"
  for f in "${LOGS[@]}"; do
    if grep -F '"tool":"Read"' "$f" 2>/dev/null | grep -F "\"file\":\"${FILE_PATH}\"" | grep -qF "\"session\":\"${SESSION_ID}\""; then
      READ_FOUND="yes"
      break
    fi
  done
fi

if [ "$READ_FOUND" = "yes" ]; then
  exit 0
fi

{
  echo "BLOCKED: Write would overwrite an existing tracked file you have not Read in this session."
  echo "  file: ${FILE_PATH}"
  echo ""
  echo "  なぜ: 新規作成のつもりの Write が、別セッションが数分前に commit した内容を"
  echo "        丸ごと消した実例がある（2026-08-12）。上書きの前に対象を見る。"
  echo ""
  echo "  【代替】"
  echo "   1. Read \"${FILE_PATH}\"   ← まず中身を見る（これだけで解除される）"
  echo "   2. 部分変更なら Write ではなく Edit を使う"
  echo "   3. 本当に全置換したいなら、Read した上で Write する"
} >&2
exit 2
