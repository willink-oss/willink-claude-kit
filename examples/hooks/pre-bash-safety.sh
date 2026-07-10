#!/usr/bin/env bash
# pre-bash-safety.sh — PreToolUse SECURITY gate for the Bash tool, FAIL-CLOSED.
#
# The production-grade sibling of pretooluse-block-example.sh: a fuller destructive-command
# denylist that scans a *stripped* copy of the command (quoted strings + heredoc bodies
# removed via _strip-command.awk) so a commit message or doc that merely mentions
# "rm -rf /" as text does not false-positive.
#
# Wire it up in .claude/settings.json (copy this file AND _strip-command.awk into .claude/hooks/):
#   "hooks": {
#     "PreToolUse": [
#       { "matcher": "Bash",
#         "hooks": [{ "type": "command", "command": ".claude/hooks/pre-bash-safety.sh" }] }
#     ]
#   }
#
# Contract (see docs/hooks-guide.md):
#   - exit 0 = allow, exit 2 = BLOCK (stderr shown to Claude).
#   - FAIL-CLOSED: on any internal error (missing awk / jq+python3, unparseable input) BLOCK.
#   - Portability: POSIX ERE (grep -E) only — no grep -P, no \s; use [[:space:]]. BSD/GNU safe.
# Known limitation: `bash -c "rm -rf /"` is NOT detected — the payload is inside a quoted
# literal that the stripper removes. Defense in depth, not a complete control.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# awk is required to strip quoted/heredoc content.
if ! command -v awk >/dev/null 2>&1; then
  echo "BLOCKED: pre-bash-safety.sh requires awk (not installed)." >&2
  exit 2
fi

# Parse .tool_input.command from stdin JSON via jq, falling back to python3 (ships with
# macOS Command Line Tools). Depending on a single CLI is a fail-closed SPOF — a missing jq
# would otherwise BLOCK every Bash call — so we degrade to python3 before failing closed.
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
  echo "This is a hook wiring bug — check .claude/settings.json." >&2
  exit 2
fi

# Strip heredoc bodies and quoted string contents before scanning.
SCAN_CMD=$(printf '%s' "$COMMAND" | awk -f "$SCRIPT_DIR/_strip-command.awk" 2>/dev/null)
if [ -z "$SCAN_CMD" ]; then
  SCAN_CMD="$COMMAND"   # stripping produced nothing usable — scan raw
fi

block() {
  echo "BLOCKED: $1" >&2
  if [ "${2:-}" != "" ]; then
    echo "Alternative: $2" >&2
  fi
  exit 2
}

# --- Pattern 1: Catastrophic rm (root/home/cwd/parent) ---
BAD_RM_PREFIX='(^|[[:space:]])rm[[:space:]]+([^[:space:]]+[[:space:]]+)*-[a-zA-Z]*[fF][a-zA-Z]*[[:space:]]+([^[:space:]]+[[:space:]]+)*'
BAD_RM_TARGETS='(/([[:space:]]|$|\*)|~([[:space:]]|$|/)|\.([[:space:]]|$)|\.\.([[:space:]]|$))'
if printf '%s ' "$SCAN_CMD" | grep -qE "${BAD_RM_PREFIX}${BAD_RM_TARGETS}"; then
  block "Destructive rm targeting /, ~, ., or .. detected." "Remove specific files by name (e.g., rm path/to/file)."
fi
if printf '%s ' "$SCAN_CMD" | grep -qE '(^|[[:space:]])rm[[:space:]]+([^[:space:]]+[[:space:]]+)*--force[[:space:]]+([^[:space:]]+[[:space:]]+)*'"$BAD_RM_TARGETS"; then
  block "Destructive rm --force targeting /, ~, ., or .. detected." "Remove specific files by name."
fi

# --- Pattern 2: Force push to main/master ---
_IS_FORCE_PUSH=false
if printf '%s ' "$SCAN_CMD" | grep -qE '(--force(-with-lease)?)([[:space:]]|$)'; then _IS_FORCE_PUSH=true; fi
if printf '%s ' "$SCAN_CMD" | grep -qE '[[:space:]]-f([[:space:]]|$)'; then _IS_FORCE_PUSH=true; fi
if [ "$_IS_FORCE_PUSH" = "true" ]; then
  if printf '%s ' "$SCAN_CMD" | grep -qE 'git[[:space:]]+push' && \
     printf '%s ' "$SCAN_CMD" | grep -qE '(main|master)([[:space:]]|$)'; then
    block "Force push to main/master is prohibited." "Open a PR from a feature branch instead."
  fi
fi

# --- Pattern 3: Direct push to main/master (non-force) ---
# Feature-branch + PR is the recommended flow. If your project pushes directly to main
# (e.g. a solo docs repo), delete this whole pattern block.
if printf '%s ' "$SCAN_CMD" | grep -qE 'git[[:space:]]+push([[:space:]]+[^[:space:]]+)*[[:space:]]+(main|master)([[:space:]]|$)'; then
  block "Direct push to main/master is prohibited." "Push to a feature branch and open a PR."
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

# --- Pattern 8: Skip hooks / bypass the pre-commit gate ---
if printf '%s' "$SCAN_CMD" | grep -qE 'git[[:space:]]+commit[[:space:]]+[^;|&]*--no-verify'; then
  block "git commit --no-verify bypasses the pre-commit quality gate." "Fix what the hook reports, don't bypass it."
fi

# All checks passed — allow execution
exit 0
