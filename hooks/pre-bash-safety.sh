#!/bin/bash
# =============================================================
# pre-bash-safety.sh — PreToolUse hook for Bash tool
# Reads Claude Code hook JSON from stdin, blocks dangerous commands.
# Exit: 0 = allow, 2 = block (fail-closed on parse error).
# Portable: macOS BSD grep compatible (no -P, no \s, no \x27).
#
# Pattern matching runs on a "stripped" version of the command where
# quoted strings and heredoc bodies have been removed, to avoid false
# positives from commit messages and documentation that mention
# destructive commands as text.
# Known limitation: `bash -c "rm -rf /"` is NOT detected because the
# command string is inside a double-quoted literal.
# =============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Dependency check: awk must be available (jq OR python3 for JSON parse) ---
if ! command -v awk >/dev/null 2>&1; then
  echo "BLOCKED: pre-bash-safety.sh requires awk (not installed)." >&2
  exit 2
fi

# --- Read hook JSON from stdin and extract the command ---
# Parse .tool_input.command with jq when available; otherwise fall back to
# python3 (ships with macOS Command Line Tools). Failing closed when neither
# exists keeps this a fail-closed security hook while removing the hard
# single-point-of-failure on jq (a missing jq previously blocked ALL Bash).
INPUT=$(cat)
if command -v jq >/dev/null 2>&1; then
  COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
elif command -v python3 >/dev/null 2>&1; then
  COMMAND=$(printf '%s' "$INPUT" | python3 -c 'import sys, json
try:
    data = json.load(sys.stdin)
    ti = data.get("tool_input") or {}
    sys.stdout.write(ti.get("command") or "")
except Exception:
    pass' 2>/dev/null)
else
  echo "BLOCKED: pre-bash-safety.sh requires jq or python3 (neither installed)." >&2
  echo "Install: brew install jq" >&2
  exit 2
fi

if [ -z "$COMMAND" ]; then
  echo "BLOCKED: pre-bash-safety.sh could not parse tool_input.command from hook stdin." >&2
  echo "This is a hook bug — check hook wiring in .claude/settings.json." >&2
  exit 2
fi

# --- Strip heredoc bodies and quoted string contents for scanning ---
SCAN_CMD=$(printf '%s' "$COMMAND" | awk -f "$SCRIPT_DIR/_strip-command.awk" 2>/dev/null)
if [ -z "$SCAN_CMD" ]; then
  # Fallback: if stripping failed (empty result), scan raw command
  SCAN_CMD="$COMMAND"
fi

block() {
  echo "BLOCKED: $1" >&2
  if [ "${2:-}" != "" ]; then
    echo "Alternative: $2" >&2
  fi
  exit 2
}

# --- Pattern 0: customer / jointly operated repository write approval ---
# UserPromptSubmit records an exact, session-scoped grant. This PreToolUse
# check is fail-closed: a protected remote mutation cannot be inferred from
# requests such as "share the link" or "prepare the files".
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
EXTERNAL_WRITE_GUARD="$ROOT_DIR/scripts/external-repo-write-guard.py"

# Approval state is hook-owned. A model/tool command may not mint or edit it.
if printf '%s' "$SCAN_CMD" | grep -qE '(\.claude/logs/external-write-approvals|external-repo-write-guard\.py[[:space:]]+capture)'; then
  block "External-repository approval state is hook-owned and cannot be created from a tool command." \
        "Ask the user for a structured approval line in their next message."
fi
if printf '%s' "$SCAN_CMD" | grep -qE '(^|[|;&][[:space:]]*)(bash[[:space:]]+)?[^[:space:]]*pre-prompt-guard\.sh([[:space:]]|$)'; then
  block "UserPromptSubmit approval capture cannot be invoked from a tool command." \
        "Ask the user for a structured approval line in their next message."
fi

# ⚠️ 外部リポ write の判定には**リポ台帳が要る**（どれが顧客リポかを知らないと判定できない）。
#    台帳は各組織のもので配布物には入らないので、**guard が無い環境では
#    このパターンだけを skip する**。他のパターンは fail-closed のまま。
#    hook 全体を落とすと、受け取った側では Bash が 1 つも通らなくなる。
#    ⚠️ skip したことは**黙らずに 1 行出す**（免除が静かに恒久化しないように）。
if [ ! -f "$EXTERNAL_WRITE_GUARD" ]; then
  printf '%s\n' "[pre-bash-safety] 外部リポ write の判定を skip（リポ台帳が無いため）。" \
                 "  台帳を用意して scripts/external-repo-write-guard.py を置くと有効になります。" >&2
elif command -v python3 >/dev/null 2>&1; then
  _EXTERNAL_GUARD_OUT=$(printf '%s' "$INPUT" | python3 "$EXTERNAL_WRITE_GUARD" guard 2>&1)
  _EXTERNAL_GUARD_RC=$?
  if [ "$_EXTERNAL_GUARD_RC" -ne 0 ]; then
    printf '%s\n' "$_EXTERNAL_GUARD_OUT" >&2
    exit 2
  fi
elif printf '%s' "$SCAN_CMD" | grep -qE 'git[[:space:]]+([^[:space:]]+[[:space:]]+)*push|gh[[:space:]]+(api|pr|issue|release|repo|workflow)|curl[[:space:]].*(-X|--request)'; then
  block "python3 is required to classify remote write targets; remote mutation blocked fail-closed." \
        "Install python3, or perform read-only inspection only."
fi

# --- Pattern 1: Catastrophic rm (root/home/cwd/parent) ---
# Matches: rm -rf / | rm -rf /* | rm -rf ~ | rm -rf ~/ | rm -rf . | rm -rf ..
BAD_RM_PREFIX='(^|[[:space:]])rm[[:space:]]+([^[:space:]]+[[:space:]]+)*-[a-zA-Z]*[fF][a-zA-Z]*[[:space:]]+([^[:space:]]+[[:space:]]+)*'
BAD_RM_TARGETS='(/([[:space:]]|$|\*)|~([[:space:]]|$|/)|\.([[:space:]]|$)|\.\.([[:space:]]|$))'
if printf '%s ' "$SCAN_CMD" | grep -qE "${BAD_RM_PREFIX}${BAD_RM_TARGETS}"; then
  block "Destructive rm targeting /, ~, ., or .. detected." "Remove specific files by name (e.g., rm path/to/file)."
fi

# Also catch --force variant
if printf '%s ' "$SCAN_CMD" | grep -qE '(^|[[:space:]])rm[[:space:]]+([^[:space:]]+[[:space:]]+)*--force[[:space:]]+([^[:space:]]+[[:space:]]+)*'"$BAD_RM_TARGETS"; then
  block "Destructive rm --force targeting /, ~, ., or .. detected." "Remove specific files by name."
fi

# --- Pattern 2: Force push to main/master ---
# ⚠️ 2026-08-25 の過検出: 判定を **コマンド行全体** に対して行っていたため、
#    `git branch -f <新ブランチ> HEAD && git push -q -u origin <feature> ; gh pr create --body "... main ..."`
#    が「force flag(-f) あり + git push あり + main あり」で block された。
#    3 つの手がかりが **別々のコマンド** から集まっていた。機械が正常に切る操作を
#    人の語彙で審査すると、正常が毎回ブロックされ回避が常態化し、
#    回避されたゲートは本物の違反も素通しする（common-mistakes の既知パターン）。
#
# 直し方: 区切り（&& || ; | 改行）で **セグメントに分け、同じセグメントの中で**
#    force flag と git push と main/master が揃ったときだけ落とす。
#    リテラルのコマンドに対して検出力は落ちない（`git push -f origin main` も
#    `cd x && git push --force origin main` も同一セグメント内で揃う）。
_force_push_to_main() {
  # tr は各区切り文字を改行へ写像する（BSD/GNU 共通）。&& / || は空セグメントになる。
  # ⚠️ printf は '%s\n' にすること。末尾に改行が無いと read が最後のセグメントを
  #    読まずに EOF で抜け、**1 件も検査されないまま素通り**する（実測で踏んだ）。
  printf '%s\n' "$1" | tr '&|;' '\n' | while IFS= read -r _seg; do
    printf '%s ' "$_seg" | grep -qE 'git[[:space:]]+push' || continue
    printf '%s ' "$_seg" | grep -qE '(main|master)([[:space:]]|$)' || continue
    if printf '%s ' "$_seg" | grep -qE '(--force([[:space:]]|=|$)|--force-with-lease|[[:space:]]-f([[:space:]]|$))'; then
      echo HIT
      break
    fi
  done | grep -q HIT
}
if _force_push_to_main "$SCAN_CMD"; then
  block "Force push to main/master is prohibited." "Open a PR from a feature branch instead."
fi

# --- Pattern 3: Direct push to main/master (non-force) ---
# 例外: 「main 直 push が正しい運用」のリポ（運用・文書リポなど）だけを許す。
#
# ⚠️ **リポ名をここに書かない**（2026-09-09 パラメータ化）。設定から読む。
#    ハードコードのまま配ると、受け取った側では例外が 1 つも効かず、
#    こちらのリポ名だけが特別扱いされた状態で出ていく。
#
#    HARNESS_DIRECT_PUSH_REPOS  カンマ区切り。origin の URL に**部分一致**したら許す。
#    既定は空 = **どのリポでも PR を要求する**（受け取った側で安全側に倒れる）。
if printf '%s ' "$SCAN_CMD" | grep -qE 'git[[:space:]]+push([[:space:]]+[^[:space:]]+)*[[:space:]]+(main|master)([[:space:]]|$)'; then
  _OPS_REMOTE=$(git remote get-url origin 2>/dev/null || true)
  _ALLOWED=0
  _ALLOW_LIST="${HARNESS_DIRECT_PUSH_REPOS:-}"
  if [ -n "$_ALLOW_LIST" ] && [ -n "$_OPS_REMOTE" ]; then
    _OLD_IFS="$IFS"; IFS=','
    for _pat in $_ALLOW_LIST; do
      [ -z "$_pat" ] && continue
      if printf '%s' "$_OPS_REMOTE" | grep -qF "$_pat"; then _ALLOWED=1; break; fi
    done
    IFS="$_OLD_IFS"
  fi
  if [ "$_ALLOWED" -eq 0 ]; then
    block "Direct push to main/master is prohibited." "Push to a feature branch and open a PR."
  fi
fi

# --- Pattern 4: git reset --hard ---
if printf '%s' "$SCAN_CMD" | grep -qE 'git[[:space:]]+reset[[:space:]]+.*--hard'; then
  block "git reset --hard discards committed changes." "Use git stash (save changes) or git revert (safe undo)."
fi

# --- Pattern 5: git clean -f (delete untracked files) ---
if printf '%s' "$SCAN_CMD" | grep -qE 'git[[:space:]]+clean[[:space:]]+(-[a-zA-Z]*f|--force)'; then
  block "git clean -f permanently deletes untracked files." "Run 'git clean -n' (dry run) first."
fi

# --- Pattern 6: Fork bomb ---
if printf '%s' "$SCAN_CMD" | grep -qE ':[[:space:]]*\([[:space:]]*\)[[:space:]]*\{[^}]*:[[:space:]]*\|[[:space:]]*:'; then
  block "Fork bomb pattern detected."
fi

# --- Pattern 7: Filesystem-destroying commands ---
if printf '%s' "$SCAN_CMD" | grep -qE '(^|[^a-zA-Z_])mkfs\.'; then
  block "mkfs (filesystem creation) is destructive." "If intentional, run manually outside Claude Code."
fi
if printf '%s' "$SCAN_CMD" | grep -qE '(^|[^a-zA-Z_])dd[[:space:]]+[^;|&]*of=/dev/'; then
  block "dd writing to /dev/ device is destructive." "If intentional, run manually outside Claude Code."
fi

# --- Pattern 8: Skip hooks / bypass signing ---
if printf '%s' "$SCAN_CMD" | grep -qE 'git[[:space:]]+commit[[:space:]]+[^;|&]*--no-verify'; then
  block "git commit --no-verify bypasses the pre-commit quality gate." "Fix the issue the hook reports, don't bypass it."
fi

# --- Pattern 9: git push --no-verify (bypasses .githooks/pre-push) ---
# standards/branch-and-release.md §3-3 は「main 直 push の強制は .githooks/pre-push が担う」と
# 定めるが、--no-verify はそのゲートを 1 フラグで丸ごと無効化する。リモート側も crew main は
# 管理者を保護から除外している構成では、直 push は CI でも
# 捕捉されない（＝この 1 フラグでゲート資産の無検査 merge が通る）。Pattern 8 の commit 形と対。
#
# 短縮形の注意: `git push -n` は --no-verify **ではなく --dry-run**（送信しない安全な確認）。
# 誤って block しないよう、ここでは長い形 --no-verify のみを見る。
# 既知の限界: Pattern 8 と同じく、コマンド文字列がクォート内にある場合（bash -c "...")は検出しない。
_GIT_GLOBAL_OPTS='(-c[[:space:]]+[^[:space:]]+[[:space:]]+|-[^[:space:]]+[[:space:]]+)*'
if printf '%s' "$SCAN_CMD" | grep -qE "git[[:space:]]+${_GIT_GLOBAL_OPTS}push[[:space:]]+[^;|&]*--no-verify"; then
  block "git push --no-verify bypasses the .githooks/pre-push gate (branch policy)." \
        "Read why the hook blocked it, then either split the commit into docs/ops-only changes (docs/ops 用のパスと prefix のみ), or push a feature branch and open a PR: git checkout -b <type>/<slug> && git push -u origin <type>/<slug> && gh pr create --base main --fill && gh pr merge --auto --squash"
fi

# --- Pattern 10: ブランチ同一性（共有 checkout で別ブランチへコミットさせない）---
# 2026-09-04: `git rev-parse --abbrev-ref HEAD` で main を確認して作業したのに、
# 数分後の commit が `docs/some-slug-20260904` に乗った。並行セッションが
# 同じ主 checkout を switch していた（同日のログに実セッション 4 本が混在）。
# 同型は 2026-07-10 にも起きている（偽 CAP 起票が main へ push・即 revert）。
# 判定は「私が作業していたブランチ（session 単位の pin）」と現在の HEAD の突合。
# ⚠️ script 不在時は **allow + 大声の警告**（全 commit を止めると self-lockout=L3 になる）。
BRANCH_PIN_GUARD="$ROOT_DIR/scripts/branch-pin-guard.sh"
if [ -x "$BRANCH_PIN_GUARD" ]; then
  # commit 以外は guard 側が黙って通す（対象判定を 1 箇所に閉じるため呼び出しは無条件）
  if ! _PIN_OUT=$(printf '%s' "$INPUT" | "$BRANCH_PIN_GUARD" --check --cmd "$SCAN_CMD" 2>&1); then
    block "$_PIN_OUT" \
          "自分の worktree を切ってそこでコミットしてください: git worktree add -q <path> -b <type>/<slug> origin/main"
  fi
  [ -n "$_PIN_OUT" ] && printf '%s\n' "$_PIN_OUT" >&2
elif printf '%s' "$SCAN_CMD" | grep -qE 'git([[:space:]]+(-[Cc][[:space:]]+[^[:space:]]+|--[^[:space:]]+|-[^[:space:]]+))*[[:space:]]+commit([[:space:]]|$)'; then
  echo "⚠️  pre-bash-safety: scripts/branch-pin-guard.sh が見つからないため、ブランチ同一性を検査できません。" >&2
  echo "    これは『同じブランチである』という意味ではありません（未検査のまま通しました）。" >&2
fi

# All checks passed — allow execution
exit 0
