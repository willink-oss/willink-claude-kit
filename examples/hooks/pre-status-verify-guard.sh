#!/usr/bin/env bash
# pre-status-verify-guard.sh — UserPromptSubmit advisory, FAIL-OPEN.
#
# When a prompt asks for status / progress / PR-state / liveness, this hook injects a
# live-state-measurement checklist as `additionalContext` so the model measures live state
# before it reports it (a document is *plan*; live is *state*). It is the prompt-time
# counterpart to the audit script `scripts/live-state-audit.sh` and the `live-state-verify-guard`
# skill, which check the same discipline after the fact.
#
# Why advisory (not a blocking gate)? A `Stop` hook can only give feedback by *blocking*, so a
# false positive is expensive (docs/hooks-guide.md). Injecting context on the next prompt is a
# gentler, reversible nudge: if this over-fires, the cost is a few extra lines of context —
# near-zero — so the keyword set is deliberately broad.
#
# Extend the trigger set without editing this file: set env STATUS_GUARD_EXTRA_KEYWORDS to an
# ERE alternation (e.g. "rollout|canary") and it is appended to the built-in keywords.
#
# Wire it up in .claude/settings.json:
#   "hooks": {
#     "UserPromptSubmit": [
#       { "hooks": [{ "type": "command", "command": ".claude/hooks/pre-status-verify-guard.sh" }] }
#     ]
#   }
#
# Contract (see docs/hooks-guide.md):
#   - FAIL-OPEN: exit 0 on EVERY path — malformed stdin, no jq AND no python3, no match.
#     An advisory hook must never disrupt the session, so it never blocks.
#   - Parses .prompt from stdin JSON via jq, degrading to python3 (ships with macOS Command
#     Line Tools) so a missing jq does not silently no-op — mirrors pre-bash-safety.sh.
#   - Emits `additionalContext` as JSON built by jq (or python3 json.dumps) so the payload is
#     always valid — a malformed blob would be dropped, defeating the reminder.
#   - Portability: POSIX ERE (grep -E) only — no grep -P, no \s; use [[:space:]]. BSD/GNU safe.
set -uo pipefail

INPUT=$(cat)

# --- Parse .prompt from stdin (jq, then python3) — fail-open, so never block on error ---
if command -v jq >/dev/null 2>&1; then
  PROMPT=$(printf '%s' "$INPUT" | jq -r '.prompt // empty' 2>/dev/null)
elif command -v python3 >/dev/null 2>&1; then
  PROMPT=$(printf '%s' "$INPUT" | python3 -c 'import sys, json
try:
    sys.stdout.write((json.load(sys.stdin).get("prompt") or ""))
except Exception:
    pass' 2>/dev/null)
else
  # No parser available — an advisory nudge is optional, so degrade to silence.
  exit 0
fi

# Empty / unparseable prompt → nothing to advise on.
[ -z "$PROMPT" ] && exit 0

# --- Keywords for status / progress / PR-state / liveness requests (case-insensitive ERE) ---
# Additive-context only (never blocks), so leaning broad costs almost nothing.
STATUS_KEYWORDS='status|progress|standup|stand-up|daily report|weekly report|is it (deployed|live|up|done|merged|released)|is (the|it|prod|the site|the service|everything) (up|down|live|deployed|working)|are we (live|done|deployed)|what.?s the (state|status)|remaining|left to do|blocker|uptime|health|deployed|is .* merged|released|open prs?|pr status|review status|report back|report on|done\?'

# Opt-in extension via env, appended as an ERE alternation.
if [ -n "${STATUS_GUARD_EXTRA_KEYWORDS:-}" ]; then
  STATUS_KEYWORDS="$STATUS_KEYWORDS|$STATUS_GUARD_EXTRA_KEYWORDS"
fi

# Match with stderr silenced: a malformed opt-in STATUS_GUARD_EXTRA_KEYWORDS must not
# leak a raw grep error. On a grep error nothing matches, so the advisory stays silent
# (fail-open) rather than blocking -- exactly the intended degradation.
printf '%s' "$PROMPT" | grep -qiE "$STATUS_KEYWORDS" 2>/dev/null || exit 0

REMINDER='[pre-status-verify-guard] Before you report status / progress / PR-state / liveness, measure live state (a document is *plan*; live is *state*):
- PR state: `gh pr view <N> --json state,mergedAt` (re-measure even when copying a prior standup into a new message — "I measured it once" is not "I measured it now").
- Service / asset: `curl -o /dev/null -s -w "%{http_code}" <url>` (do not infer "up" from a document).
- External-outage hypothesis: run `gh api` / your cloud CLI once before recording it ([hypothesis] -> [evidence] -> [verdict]). "Not working" is not "broken".
- Config / state-table docs: read them in full (no line limit) so a status table in the middle or at the end is not missed.
An empty probe result is "unknown", never "zero".'

# Build the additionalContext JSON with a real serializer so it is always valid.
if command -v jq >/dev/null 2>&1; then
  jq -nc --arg ctx "$REMINDER" \
    '{hookSpecificOutput:{hookEventName:"UserPromptSubmit",additionalContext:$ctx}}'
else
  REMINDER="$REMINDER" python3 -c 'import os, json
ctx = os.environ.get("REMINDER", "")
print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}}))' 2>/dev/null
fi

exit 0
