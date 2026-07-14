#!/usr/bin/env bash
# Example PreToolUse hook — SECURITY gate, FAIL-CLOSED.
#
# Wire it up in .claude/settings.json:
#   "hooks": {
#     "PreToolUse": [
#       { "matcher": "Bash",
#         "hooks": [{ "type": "command", "command": ".claude/hooks/pretooluse-block-example.sh" }] }
#     ]
#   }
#
# Contract (see docs/hooks-guide.md):
#   - Input arrives as JSON on STDIN (not env vars); parse with jq.
#   - exit 0 → allow the tool call.  exit 2 → BLOCK it and show stderr to Claude.
#   - Security (Pre*) hooks FAIL-CLOSED: on any internal error (missing jq,
#     unparseable input) we BLOCK rather than silently allow.
#   - Portability: POSIX ERE via `grep -E` only — no Perl-mode matching and no
#     \s/\d/\w escapes, which BSD grep on macOS does not support.
set -uo pipefail

fail_closed() {
  printf 'pretooluse hook error: %s — blocking (fail-closed)\n' "$1" >&2
  exit 2
}

command -v jq >/dev/null 2>&1 || fail_closed "jq not found"

# Read stdin. Both reads are guarded so any failure BLOCKS (fail-closed) — a security
# gate must never continue past a read error and silently allow.
input="$(cat)" || fail_closed "could not read hook input"
[ -n "$input" ] || fail_closed "empty hook input"
tool="$(printf '%s' "$input" | jq -r '.tool_name // empty')" || fail_closed "unparseable hook input"
cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // empty')" || fail_closed "unparseable tool_input"

# Only gate Bash commands; allow every other tool through. A well-formed payload that
# simply is not a Bash command has nothing to gate → allow. For real use also assert
# .tool_input.command is a STRING — a non-string array could carry a command this
# template's grep would not see.
[ "$tool" = "Bash" ] || exit 0

# Illustrative denylist — EXTEND for real use; a teaching template, not a complete
# control. POSIX ERE only (grep -E). Bundled short flags only (-rf / -fr); split
# (`-r -f`) and long (`--force`) forms are intentionally out of scope. Caught:
#   1. rm -rf of /, ~, $HOME, or a top-level system dir (/etc, /usr, /var, …)
#   2. curl/wget … | sh   (pipe remote content straight into a shell)
#   3. chmod 777 / 0777    (world-writable)
deny='(^|[[:space:]])rm[[:space:]]+-[[:alpha:]]*f[[:alpha:]]*[[:space:]]+(/|~|\$HOME)([[:space:]]|$)'
deny="$deny"'|(^|[[:space:]])rm[[:space:]]+-[[:alpha:]]*f[[:alpha:]]*[[:space:]]+/(etc|usr|var|bin|lib|boot|sys|dev|opt|root|sbin)([[:space:]/]|$)'
deny="$deny"'|(curl|wget)[[:space:]].*\|[[:space:]]*(ba|z)?sh([[:space:]]|$)'
deny="$deny"'|chmod[[:space:]]+-?[Rr]?[[:space:]]*0?777'

if printf '%s' "$cmd" | grep -qE -- "$deny"; then
  printf 'blocked dangerous command: %s\n' "$cmd" >&2
  exit 2
fi

exit 0
