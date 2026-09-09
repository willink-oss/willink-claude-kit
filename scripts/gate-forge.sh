#!/usr/bin/env bash
# =============================================================
# gate-forge.sh — ミス記述 → fail-closed hook の scaffold ジェネレータ（D01）
#
# 「文書ベースの状態推定」や「破壊コマンド」など再発したミスを機械的に止めるための
# PreToolUse fail-closed hook（stdin JSON を読み、危険入力を exit 2 で block）と、
# その block/pass を検証する test-hooks ケース雛形を **staging ディレクトリに生成** する。
#
# ⚠️ この skill/スクリプトは既存の .claude/hooks/* ・ .claude/settings.json ・ .githooks/
#    を一切変更しない（生成のみ）。生成物を実配線して tool を block させる行為は
#    self-lockout（Level 3）なので、staging → hooks 配置 + settings 登録は 責任者 承認の上、
#    人手（/update-config）で行う。WIRING.md に手順を印字する。
#
# 使い方:
#   gate-forge.sh --name <slug> [--tool Bash|Write|Edit] [--field command|file_path] \
#                 --mistake "<止めたいミスの一文>" [--pattern "<block する固定文字列>"] \
#                 [--regex] [--block-msg "<理由>"] [--alt "<安全な代替>"] \
#                 [--out-dir <staging dir>] [--force]
#   gate-forge.sh --self-test        # hermetic 決定論テスト（exit 0 で pass・gh/aws 非依存）
#
# 生成物（<out>/ 配下）:
#   pre-<slug>.sh              fail-closed PreToolUse hook（chmod +x・stdin JSON・exit 2）
#   test-<slug>.cases.sh       block/pass/fail-closed の 3 ケース（standalone 実走可）
#   WIRING.md                  配線手順 + L3/self-lockout 警告（適用は人手）
#
# 設計:
#   - hook 本体は house 既存 fail-closed hook（pre-file-protect / pre-bash-safety）と同形:
#     jq → python3 フォールバックで stdin JSON を parse、parse 失敗は fail-closed(exit 2)。
#   - --self-test は temp に **実生成** し ① validate_hook（必須要素 + bash -n）② 負制御
#     （壊した hook は validate が落ちる）③ 実挙動（match→2 / benign→0 / empty→2）を回す。
#     ハードコード成功を排除（原則 P1 自己申告禁止）。
#   - 依存は bash + python3 標準ライブラリのみ・macOS/BSD 互換（grep -P 不使用）。
# =============================================================
set -uo pipefail

NAME=""; TOOL="Bash"; FIELD=""; MISTAKE=""; PATTERN=""; MODE="fixed"
BLOCKMSG=""; ALT=""; OUTDIR=""; FORCE=0; SELFTEST=0

while [ $# -gt 0 ]; do
  case "$1" in
    --name)      NAME="$2"; shift 2 ;;
    --tool)      TOOL="$2"; shift 2 ;;
    --field)     FIELD="$2"; shift 2 ;;
    --mistake)   MISTAKE="$2"; shift 2 ;;
    --pattern)   PATTERN="$2"; shift 2 ;;
    --regex)     MODE="ere"; shift ;;
    --block-msg) BLOCKMSG="$2"; shift 2 ;;
    --alt)       ALT="$2"; shift 2 ;;
    --out-dir)   OUTDIR="$2"; shift 2 ;;
    --force)     FORCE=1; shift ;;
    --self-test) SELFTEST=1; shift ;;
    *) echo "gate-forge: unknown arg: $1" >&2; exit 3 ;;
  esac
done

# ------------------------------------------------------------
# field_for_tool <tool> — tool 名から parse する tool_input のキーを決める
# ------------------------------------------------------------
field_for_tool() {
  case "$1" in
    Bash) echo "command" ;;
    Write|Edit|"Write|Edit"|MultiEdit) echo "file_path" ;;
    *) echo "" ;;
  esac
}

# ------------------------------------------------------------
# gen_hook <target> <hookname> <tool> <fieldkey> <mistake> <pattern> <mode> <blockmsg> <alt>
#   fail-closed PreToolUse hook を生成（chmod +x）。match セクションは python が組む。
# ------------------------------------------------------------
gen_hook() {
  local target="$1" hookname="$2" tool="$3" fieldkey="$4" mistake="$5" \
        pattern="$6" mode="$7" blockmsg="$8" alt="$9"
  cat > "$target" <<'HOOK'
#!/bin/bash
# =============================================================
# __HOOK_FILE__ — PreToolUse hook for __TOOL__ tool (gate-forge generated scaffold)
# Mistake to prevent: __MISTAKE__
# Reads Claude Code hook JSON from stdin, blocks matching input.
# Exit: 0 = allow, 2 = block (fail-closed on parse error).
# Portable: macOS BSD grep compatible (no -P, no \s, no \x27).
# =============================================================
set -uo pipefail

# --- Read hook JSON from stdin and extract the guarded field ---
# Parse .tool_input.__FIELD_KEY__ with jq when available; otherwise fall back to
# python3 (ships with macOS Command Line Tools). Failing closed when neither
# exists keeps this a fail-closed security hook (no single-point-of-failure on jq).
INPUT=$(cat)
if command -v jq >/dev/null 2>&1; then
  FIELD=$(printf '%s' "$INPUT" | jq -r '.tool_input.__FIELD_KEY__ // empty' 2>/dev/null)
elif command -v python3 >/dev/null 2>&1; then
  FIELD=$(printf '%s' "$INPUT" | python3 -c 'import sys, json
try:
    data = json.load(sys.stdin)
    ti = data.get("tool_input") or {}
    sys.stdout.write(ti.get("__FIELD_KEY__") or "")
except Exception:
    pass' 2>/dev/null)
else
  echo "BLOCKED: __HOOK_FILE__ requires jq or python3 (neither installed)." >&2
  echo "Install: brew install jq" >&2
  exit 2
fi

if [ -z "$FIELD" ]; then
  echo "BLOCKED: __HOOK_FILE__ could not parse tool_input.__FIELD_KEY__ from hook stdin." >&2
  echo "This is a hook bug — check hook wiring in .claude/settings.json." >&2
  exit 2
fi

block() {
  echo "BLOCKED: $1" >&2
  if [ "${2:-}" != "" ]; then
    echo "Alternative: $2" >&2
  fi
  exit 2
}

__MATCH_SECTION__

# All checks passed — allow execution
exit 0
HOOK

  python3 - "$target" "$hookname" "$tool" "$fieldkey" "$mistake" \
           "$pattern" "$mode" "$blockmsg" "$alt" <<'PY'
import sys, shlex
(path, hookname, tool, fieldkey, mistake, pattern, mode, blockmsg, alt) = sys.argv[1:10]
mistake_1line = " ".join(mistake.split()) or "(記述なし — WHY を書くこと)"
if not blockmsg:
    blockmsg = mistake_1line
if pattern:
    grepflag = "-Eq" if mode == "ere" else "-Fq"
    kind = "ERE regex" if mode == "ere" else "fixed-string"
    match = (
        "# --- Block pattern derived from the mistake (%s match) ---\n"
        "PATTERN=%s\n"
        "if printf '%%s' \"$FIELD\" | grep %s -- \"$PATTERN\"; then\n"
        "  block %s %s\n"
        "fi"
    ) % (kind, shlex.quote(pattern), grepflag,
         shlex.quote(blockmsg), shlex.quote(alt))
else:
    match = (
        "# --- TODO: implement the block pattern for this mistake ---\n"
        "# Mistake to prevent: %s\n"
        "# Fill a fixed-string (or ERE) pattern, then uncomment:\n"
        "#   PATTERN='<dangerous substring>'\n"
        "#   if printf '%%s' \"$FIELD\" | grep -Fq -- \"$PATTERN\"; then\n"
        "#     block 'why the 運用者/責任者 cares' 'the safe alternative'\n"
        "#   fi\n"
        "# NOTE: until a pattern is added this hook only fails closed on unparseable\n"
        "# input; it does not yet block the mistake."
    ) % mistake_1line

s = open(path).read()
s = (s.replace("__HOOK_FILE__", hookname)
      .replace("__TOOL__", tool)
      .replace("__FIELD_KEY__", fieldkey)
      .replace("__MISTAKE__", mistake_1line)
      .replace("__MATCH_SECTION__", match))
open(path, "w").write(s)
PY
  chmod +x "$target"
}

# ------------------------------------------------------------
# gen_cases <target> <slug> <hookfile> <tool> <fieldkey> <pattern>
#   block/pass/fail-closed の 3 ケース。standalone 実走可（$1 で hook パス上書き）。
# ------------------------------------------------------------
gen_cases() {
  local target="$1" slug="$2" hookfile="$3" tool="$4" fieldkey="$5" pattern="$6"
  cat > "$target" <<'CASES'
#!/bin/bash
# =============================================================
# test-__SLUG__.cases.sh — block/pass cases for __HOOK_FILE__ (gate-forge generated)
# Merge these into .claude/hooks/test-hooks.sh (add a runner like run_file_hook),
# OR run standalone:
#   bash test-__SLUG__.cases.sh [path-to-hook]   # exit 0 = all cases pass
# =============================================================
set -uo pipefail
HOOK="${1:-$(cd "$(dirname "$0")" && pwd)/__HOOK_FILE__}"
PASS=0; FAIL=0
_case() { # json expected label
  local rc; printf '%s' "$1" | "$HOOK" >/dev/null 2>&1; rc=$?
  if [ "$rc" = "$2" ]; then PASS=$((PASS+1));
  else FAIL=$((FAIL+1)); echo "  FAIL [$3] expected=$2 got=$rc" >&2; fi
}
__BLOCK_CASE__
# PASS case — benign input must be allowed (exit 0)
_case __PASS_ARG__ 0 'gate-forge __SLUG__ allows benign input'
# fail-closed — unparseable/empty field must block (exit 2)
_case __EMPTY_ARG__ 2 'gate-forge __SLUG__ fails closed on empty field'
if [ "$FAIL" -eq 0 ]; then echo "✅ test-__SLUG__ cases PASS ($PASS)"; exit 0; fi
echo "🛑 test-__SLUG__ cases FAIL ($FAIL)"; exit 1
CASES

  python3 - "$target" "$slug" "$hookfile" "$tool" "$fieldkey" "$pattern" <<'PY'
import sys, json, shlex
(path, slug, hookfile, tool, fieldkey, pattern) = sys.argv[1:7]

def hook_json(value):
    return json.dumps({"tool_name": tool, "tool_input": {fieldkey: value}})

pass_val = "SAFE_BENIGN_VALUE_gateforge_%s" % fieldkey
empty_json = json.dumps({"tool_name": tool, "tool_input": {}})

if pattern:
    block_json = hook_json(pattern)  # verbatim pattern => fixed-string match hits
    block_case = (
        "# BLOCK case — input that reproduces the mistake must be blocked (exit 2)\n"
        "_case %s 2 'gate-forge %s blocks the mistake'"
    ) % (shlex.quote(block_json), slug)
else:
    block_case = (
        "# BLOCK case — TODO: add a JSON input that reproduces the mistake and expect 2\n"
        "# _case '{\"tool_name\":\"%s\",\"tool_input\":{\"%s\":\"<dangerous>\"}}' 2 "
        "'gate-forge %s blocks the mistake'"
    ) % (tool, fieldkey, slug)

s = open(path).read()
s = (s.replace("__SLUG__", slug)
      .replace("__HOOK_FILE__", hookfile)
      .replace("__BLOCK_CASE__", block_case)
      .replace("__PASS_ARG__", shlex.quote(hook_json(pass_val)))
      .replace("__EMPTY_ARG__", shlex.quote(empty_json)))
open(path, "w").write(s)
PY
  chmod +x "$target"
}

# ------------------------------------------------------------
# gen_wiring <target> <slug> <hookfile> <tool>
# ------------------------------------------------------------
gen_wiring() {
  local target="$1" slug="$2" hookfile="$3" tool="$4"
  cat > "$target" <<WIRING
# 配線手順 — ${hookfile}（gate-forge 生成 scaffold）

> ⚠️ **fail-closed hook の実配線は self-lockout = Level 3。責任者 事前承認が必須。**
> gate-forge は staging に **生成するだけ**で、hooks 配置・settings 登録は一切しない。
> 壊れた fail-closed hook は全 ${tool} 呼び出しを止めうるため、下記は人手で慎重に行う。

## 手順（責任者 承認後・人手）

1. hook 本体の \`# --- TODO ...\` を実装する（pattern を確定し block 条件を書く）。
2. block/pass を検証（副作用なし・実走）:
   \`\`\`sh
   bash test-${slug}.cases.sh ./${hookfile}
   \`\`\`
   → \`✅ ... PASS\` を確認（block=2 / pass=0 / empty=2 が揃うこと）。
3. hook を配置（**既存 hook は上書きしない**・新規名で）:
   \`\`\`sh
   cp ${hookfile} .claude/hooks/${hookfile}
   chmod +x .claude/hooks/${hookfile}
   \`\`\`
4. \`.claude/hooks/test-hooks.sh\` に block/pass ケースを追記（\`test-${slug}.cases.sh\`
   の 2 呼び出しを既存 runner 形式に合わせて移植）。**先にセルフテストを増やしてから配線**
   （house 規約: フック導入はセルフテスト必須）。
5. settings.json への登録は **\`/update-config\` で**（直接 Edit は pre-file-protect が block）。
   PreToolUse / matcher="${tool}" に command 追加:
   \`\`\`
   "\$CLAUDE_PROJECT_DIR"/.claude/hooks/${hookfile}
   \`\`\`
6. 登録後 \`bash .claude/hooks/test-hooks.sh\` 全通過を実測してから完了とする（原則 P1）。

## ロールバック
- settings.json から当該 command 行を \`/update-config\` で除去 → \`.claude/hooks/${hookfile}\` を削除。
WIRING
}

# ------------------------------------------------------------
# validate_hook <file> — 生成 hook が fail-closed 必須要素を全て持つか（+ bash -n）
#   return 0 = OK / 非 0 = 欠落（負制御 self-test で「本当に落ちる」ことを担保）
# ------------------------------------------------------------
validate_hook() {
  local f="$1" missing=""
  bash -n "$f" 2>/dev/null || { echo "bash-n-failed"; return 1; }
  head -1 "$f" | grep -qF '#!/bin/bash'   || missing="$missing shebang"
  grep -qF 'INPUT=$(cat)' "$f"            || missing="$missing stdin-read"
  grep -qF 'jq -r' "$f"                   || missing="$missing jq-parse"
  grep -qF 'python3' "$f"                 || missing="$missing py-fallback"
  grep -qF 'exit 2' "$f"                  || missing="$missing exit2"
  grep -qF 'exit 0' "$f"                  || missing="$missing exit0"
  grep -qF 'block()' "$f"                 || missing="$missing block-helper"
  if [ -n "$missing" ]; then echo "missing:$missing"; return 1; fi
  return 0
}

# ------------------------------------------------------------
# --self-test（hermetic・実生成 + validate + 負制御 + 実挙動）
# ------------------------------------------------------------
if [ "$SELFTEST" = "1" ]; then
  tmp="$(mktemp -d -t gate-forge-selftest.XXXXXX)"
  trap 'rm -rf "$tmp"' EXIT
  fail=0

  run_case() { # <hook> <json> <expected> <label>
    local rc; printf '%s' "$2" | "$1" >/dev/null 2>&1; rc=$?
    if [ "$rc" != "$3" ]; then echo "FAIL: $4 (expected=$3 got=$rc)"; fail=1; fi
  }

  # ---- 1) Bash hook（固定文字列パターン）を実生成 ----
  bh="$tmp/pre-selftest-bash.sh"
  gen_hook "$bh" "pre-selftest-bash.sh" "Bash" "command" \
    "git reset --hard は履歴を破壊する（stash/revert を使う）" \
    "git reset --hard" "fixed" "git reset --hard は禁止" "git stash / git revert"
  if validate_hook "$bh" >/dev/null; then :; else echo "FAIL: generated bash hook missing required elements: $(validate_hook "$bh")"; fail=1; fi
  grep -qF 'git reset --hard は履歴を破壊する' "$bh" || { echo "FAIL: mistake text not embedded (WHY missing)"; fail=1; }

  # 実挙動: match → 2 / benign → 0 / empty → 2（fail-closed parse）
  run_case "$bh" '{"tool_name":"Bash","tool_input":{"command":"git reset --hard HEAD~1"}}' 2 "bash match should block"
  run_case "$bh" '{"tool_name":"Bash","tool_input":{"command":"git status"}}'              0 "bash benign should pass"
  run_case "$bh" '{"tool_name":"Bash","tool_input":{}}'                                     2 "bash empty field fails closed"
  run_case "$bh" 'not json at all'                                                          2 "bash unparseable fails closed"

  # ---- 2) 負制御 — 必須要素を落とした hook は validate が落ちる（検証器が本物）----
  broken1="$tmp/broken-no-exit2.sh"; grep -v 'exit 2' "$bh" > "$broken1"
  if validate_hook "$broken1" >/dev/null 2>&1; then echo "FAIL: validator passed a hook with NO exit 2 (broken/hardcoded)"; fail=1; fi
  broken2="$tmp/broken-no-stdin.sh"; grep -v 'INPUT=$(cat)' "$bh" > "$broken2"
  if validate_hook "$broken2" >/dev/null 2>&1; then echo "FAIL: validator passed a hook with NO stdin read (broken/hardcoded)"; fail=1; fi

  # ---- 3) Write hook（file_path フィールド）----
  wh="$tmp/pre-selftest-write.sh"
  gen_hook "$wh" "pre-selftest-write.sh" "Write" "file_path" \
    "本番 env ファイルを編集しない" ".env.production" "fixed" \
    "本番 env の編集は禁止" ".env.example を編集する"
  validate_hook "$wh" >/dev/null || { echo "FAIL: generated write hook invalid"; fail=1; }
  run_case "$wh" '{"tool_name":"Write","tool_input":{"file_path":"/app/.env.production"}}' 2 "write match should block"
  run_case "$wh" '{"tool_name":"Write","tool_input":{"file_path":"/app/README.md"}}'       0 "write benign should pass"

  # ---- 4) cases ファイルを生成 → bash -n → standalone 実走（block+pass+empty 内蔵検証）----
  cf="$tmp/test-selftest-bash.cases.sh"
  gen_cases "$cf" "selftest-bash" "pre-selftest-bash.sh" "Bash" "command" "git reset --hard"
  bash -n "$cf" || { echo "FAIL: cases file bash -n syntax error"; fail=1; }
  if bash "$cf" "$bh" >/dev/null 2>&1; then :; else echo "FAIL: generated cases file did not pass against its hook"; fail=1; fi

  # ---- 5) TODO モード（pattern 省略）でも plumbing は健全（validate + empty→2 + benign→0）----
  th="$tmp/pre-selftest-todo.sh"
  gen_hook "$th" "pre-selftest-todo.sh" "Bash" "command" "まだ pattern 未定の観測 hook" "" "fixed" "" ""
  validate_hook "$th" >/dev/null || { echo "FAIL: TODO-mode hook lost required plumbing"; fail=1; }
  run_case "$th" '{"tool_name":"Bash","tool_input":{}}'                          2 "todo-mode empty fails closed"
  run_case "$th" '{"tool_name":"Bash","tool_input":{"command":"echo hi"}}'       0 "todo-mode benign passes (no pattern yet)"

  # ---- 6) ERE モード ----
  eh="$tmp/pre-selftest-ere.sh"
  gen_hook "$eh" "pre-selftest-ere.sh" "Bash" "command" "rm -rf のルート削除を止める" \
    "rm -rf +/($|[^.])" "ere" "破壊的削除は禁止" "対象を明示する"
  validate_hook "$eh" >/dev/null || { echo "FAIL: ERE hook invalid"; fail=1; }
  grep -qF 'grep -Eq' "$eh" || { echo "FAIL: ERE mode did not emit grep -Eq"; fail=1; }
  run_case "$eh" '{"tool_name":"Bash","tool_input":{"command":"rm -rf /var/data"}}' 2 "ere match should block"
  run_case "$eh" '{"tool_name":"Bash","tool_input":{"command":"ls -la"}}'           0 "ere benign should pass"

  if [ "$fail" -eq 0 ]; then
    echo "✅ gate-forge --self-test PASS (gen→validate / 負制御×2 / Bash+Write+ERE 実挙動 / cases 実走 / TODO plumbing)"
    exit 0
  fi
  echo "🛑 gate-forge --self-test FAIL"
  exit 1
fi

# ------------------------------------------------------------
# 通常生成
# ------------------------------------------------------------
[ -n "$NAME" ] || { echo "gate-forge: --name is required (or --self-test)" >&2; exit 3; }
# name は kebab（ファイル名の一部）。先頭 pre- は許容し正規化する。
SLUG="${NAME#pre-}"
case "$SLUG" in
  ""|*[!a-z0-9-]*) echo "gate-forge: --name must be kebab-case [a-z0-9-] (optionally pre- prefixed): '$NAME'" >&2; exit 3 ;;
esac
[ -n "$MISTAKE" ] || { echo "gate-forge: --mistake \"<止めたいミスの一文>\" is required (or --self-test)" >&2; exit 3; }

# field は tool から導出（--field で明示上書き可）
if [ -z "$FIELD" ]; then
  FIELD="$(field_for_tool "$TOOL")"
fi
[ -n "$FIELD" ] || { echo "gate-forge: cannot derive tool_input field for --tool '$TOOL'; pass --field <command|file_path>" >&2; exit 3; }

HOOK_FILE="pre-${SLUG}.sh"
CASES_FILE="test-${SLUG}.cases.sh"

if [ -n "$OUTDIR" ]; then
  OUT="$OUTDIR"
else
  . "$(dirname "$0")/_phroot.sh"
  ROOT="$(ph_target_root)"
  OUT="$ROOT/.gate-forge-staging/${SLUG}"
fi
mkdir -p "$OUT"

hook_out="$OUT/$HOOK_FILE"
cases_out="$OUT/$CASES_FILE"
wiring_out="$OUT/WIRING.md"

if [ "$FORCE" -ne 1 ]; then
  for f in "$hook_out" "$cases_out" "$wiring_out"; do
    [ -e "$f" ] && { echo "gate-forge: refuse to overwrite existing $f (use --force or a fresh --out-dir)" >&2; exit 4; }
  done
fi

gen_hook   "$hook_out"  "$HOOK_FILE" "$TOOL" "$FIELD" "$MISTAKE" "$PATTERN" "$MODE" "$BLOCKMSG" "$ALT"
gen_cases  "$cases_out" "$SLUG" "$HOOK_FILE" "$TOOL" "$FIELD" "$PATTERN"
gen_wiring "$wiring_out" "$SLUG" "$HOOK_FILE" "$TOOL"

if validate_hook "$hook_out" >/dev/null; then
  echo "gate-forge: generated fail-closed hook scaffold (validated):"
else
  echo "gate-forge: ⚠️ generated but hook FAILED validation: $(validate_hook "$hook_out")" >&2
fi
echo "  hook   : $hook_out"
echo "  cases  : $cases_out"
echo "  wiring : $wiring_out"
echo ""
if [ -z "$PATTERN" ]; then
  echo "NOTE: --pattern 未指定 = TODO モード。hook の # TODO と cases の BLOCK case を実装すること。"
fi
echo "次の手順（配線は self-lockout=L3・責任者 承認後に人手）:"
echo "  1. hook の TODO を実装（--pattern を渡していれば実装済）"
echo "  2. 検証: bash $cases_out $hook_out   （✅ PASS を確認）"
echo "  3. 配線手順: $wiring_out を参照（cp → test-hooks.sh に移植 → /update-config で settings 登録）"
