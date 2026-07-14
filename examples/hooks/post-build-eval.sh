#!/usr/bin/env bash
# post-build-eval.sh — PostToolUse advisory for the Bash tool, FAIL-OPEN.
#
# Evaluator: when a test/lint/build/typecheck command fails, nudge the
# read-stderr -> fix -> re-run -> independent /review loop. Also warns on very large
# staged/committed diffs. Never blocks — advisory only (exit 0 always).
#
# Wire it up in .claude/settings.json:
#   "hooks": {
#     "PostToolUse": [
#       { "matcher": "Bash",
#         "hooks": [{ "type": "command", "command": ".claude/hooks/post-build-eval.sh" }] }
#     ]
#   }
#
# Contract (see docs/hooks-guide.md): advisory (Post*) hooks FAIL-OPEN — any problem
# exits 0 so the session is never disrupted. Portability: POSIX ERE (grep -E), no \s.
#
# Recognized commands: (npm|pnpm|yarn) [run] (test|lint|build|typecheck) · flutter
# (test|analyze) · pytest / python -m pytest · [npx] tsc · cargo (test|build|check).
set -uo pipefail

# jq absent → skip silently (fail-open).
command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat)
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
# Claude Code has used both exit_code and exitCode historically — accept either.
EXIT_CODE=$(printf '%s' "$INPUT" | jq -r '.tool_response.exit_code // .tool_response.exitCode // empty' 2>/dev/null)

[ "$TOOL_NAME" = "Bash" ] || exit 0
[ -n "$COMMAND" ] && [ -n "$EXIT_CODE" ] || exit 0

# --- Large-change advisory on successful git add/commit -------------------------------
GIT_COMMIT_PATTERN='(^|[[:space:]])git[[:space:]]+(add|commit)([[:space:]]|$)'
if [ "$EXIT_CODE" = "0" ] && printf '%s' "$COMMAND" | grep -qE "$GIT_COMMIT_PATTERN"; then
  STAGED_STAT=$(git diff --cached --shortstat 2>/dev/null)
  [ -z "$STAGED_STAT" ] && STAGED_STAT=$(git diff HEAD^ HEAD --shortstat 2>/dev/null)
  if [ -n "$STAGED_STAT" ]; then
    CHANGED_LINES=$(printf '%s' "$STAGED_STAT" | awk '
      {ins=0;del=0;
       for(i=1;i<=NF;i++){ if($(i+1)~/^insertion/)ins=$i; if($(i+1)~/^deletion/)del=$i }}
      END{print ins+del+0}')
    if [ "${CHANGED_LINES:-0}" -gt 500 ] 2>/dev/null; then
      cat >&2 <<EOF
================================================
 [post-build-eval] large-change advisory
================================================
 Command:      $COMMAND
 Changed lines: ${CHANGED_LINES} (insertions + deletions)
 This is a large change (>500 lines). Consider splitting into logical commits.
   e.g. keep "feat: X" and "refactor: Y" as separate commits.
 (advisory only — the commit is not blocked)
================================================
EOF
    fi
  fi
fi

# Success → nothing to evaluate.
[ "$EXIT_CODE" = "0" ] && exit 0

# --- Is this a test/lint/build/typecheck command? ------------------------------------
TARGET_PATTERN='(^|[[:space:]])(npm|pnpm|yarn)([[:space:]]+run)?[[:space:]]+(test|lint|build|typecheck)([[:space:]]|$)|(^|[[:space:]])flutter[[:space:]]+(test|analyze)([[:space:]]|$)|(^|[[:space:]])py(thon[[:space:]]+-m[[:space:]]+py)?test([[:space:]]|$)|(^|[[:space:]])(npx[[:space:]]+)?tsc([[:space:]]|$)|(^|[[:space:]])cargo[[:space:]]+(test|build|check)([[:space:]]|$)'
printf '%s' "$COMMAND" | grep -qE "$TARGET_PATTERN" || exit 0

# --- Emit Evaluator notification (advisory) ------------------------------------------
cat >&2 <<EOF
================================================
 Evaluator (post-build-eval.sh)
================================================
 Command:   $COMMAND
 Exit code: $EXIT_CODE
 A test / lint / build / typecheck step FAILED.

 Next:
   1. Read the failure (stderr) and locate the cause.
   2. Fix it.
   3. Re-run the SAME command and confirm PASS.
   4. On PASS, proceed to commit.
   5. After committing, run /review in a fresh session
      (Generator-Verifier: independent review avoids familiarity bias).

 (self-evaluation step — fail-open: the flow is not blocked)
================================================
EOF

exit 0
