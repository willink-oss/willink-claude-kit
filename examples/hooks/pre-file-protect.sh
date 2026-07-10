#!/usr/bin/env bash
# pre-file-protect.sh — PreToolUse SECURITY gate for Write/Edit tools, FAIL-CLOSED.
#
# Blocks Write/Edit of sensitive files: .env (values), credential/key files, .git
# internals, and self-modification of .claude/settings.json.
#
# Wire it up in .claude/settings.json:
#   "hooks": {
#     "PreToolUse": [
#       { "matcher": "Write|Edit",
#         "hooks": [{ "type": "command", "command": ".claude/hooks/pre-file-protect.sh" }] }
#     ]
#   }
#
# Contract (see docs/hooks-guide.md):
#   - exit 0 = allow, exit 2 = BLOCK. FAIL-CLOSED on parse error (jq or python3 fallback).
set -uo pipefail

# Parse .tool_input.file_path from stdin JSON; jq preferred, python3 fallback (no single-CLI SPOF).
INPUT=$(cat)
if command -v jq >/dev/null 2>&1; then
  FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
elif command -v python3 >/dev/null 2>&1; then
  FILE_PATH=$(printf '%s' "$INPUT" | python3 -c 'import sys, json
try:
    data = json.load(sys.stdin)
    ti = data.get("tool_input") or {}
    sys.stdout.write(ti.get("file_path") or "")
except Exception:
    pass' 2>/dev/null)
else
  echo "BLOCKED: pre-file-protect.sh requires jq or python3 (neither installed)." >&2
  echo "Install: brew install jq" >&2
  exit 2
fi

if [ -z "$FILE_PATH" ]; then
  echo "BLOCKED: pre-file-protect.sh could not parse tool_input.file_path from hook stdin." >&2
  echo "This is a hook wiring bug — check .claude/settings.json." >&2
  exit 2
fi

FILENAME=$(basename "$FILE_PATH")

block() {
  echo "BLOCKED: $1" >&2
  if [ "${2:-}" != "" ]; then
    echo "Alternative: $2" >&2
  fi
  exit 2
}

# --- Pattern 1: .env files (allow .env.example / .env.sample) ---
case "$FILENAME" in
  .env|.env.*)
    case "$FILENAME" in
      .env.example|.env.sample) : ;;  # allowed
      *) block ".env files may contain secrets." "Edit .env.example (keys only, no values) and document in README." ;;
    esac
    ;;
esac

# --- Pattern 2: Credential / secret / key files (basename-based) ---
# Match by basename to avoid false positives like "secretary" in directory names.
FN_LOWER=$(printf '%s' "$FILENAME" | tr '[:upper:]' '[:lower:]')
case "$FN_LOWER" in
  credential|credentials|credential.*|credentials.*|credential-*|credentials-*|credential_*|credentials_*)
    block "Editing credential/secret/key files is prohibited." "Use a secrets manager (AWS Secrets Manager, GitHub Secrets, Vault, etc.)." ;;
  secret|secrets|secret.*|secrets.*|secret-*|secrets-*|secret_*|secrets_*)
    block "Editing credential/secret/key files is prohibited." "Use a secrets manager (AWS Secrets Manager, GitHub Secrets, Vault, etc.)." ;;
  id_rsa|id_rsa.*|id_ed25519|id_ed25519.*|id_ecdsa|id_ecdsa.*)
    block "Editing credential/secret/key files is prohibited." "Use a secrets manager or your SSH agent." ;;
  serviceaccountkey.json|firebase-adminsdk*.json)
    block "Editing credential/secret/key files is prohibited." "Use a secrets manager." ;;
  *.pem|*.p12|*.pfx|*.key)
    block "Editing credential/secret/key files is prohibited." "Use a secrets manager." ;;
esac

# --- Pattern 2b: .aws/credentials (path-level) ---
case "$FILE_PATH" in
  */.aws/credentials|*/.aws/credentials.*)
    block "Editing credential/secret/key files is prohibited." "Use a secrets manager." ;;
esac

# --- Pattern 3: Harness self-modification prevention ---
case "$FILE_PATH" in
  *.claude/settings.json|*.claude/settings.local.json)
    block "Direct modification of .claude/settings.json is prohibited." "Use the /update-config skill, or edit it manually with an atomic write (jq + mv)." ;;
esac

# --- Pattern 4: .git internal files ---
case "$FILE_PATH" in
  */.git/config|*/.git/HEAD|*/.git/objects/*|*/.git/refs/*|*/.git/index)
    block "Direct modification of .git internals is prohibited." "Use git commands (git config, git update-ref, etc.)." ;;
esac

# All checks passed — allow execution
exit 0
