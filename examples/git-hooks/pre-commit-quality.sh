#!/usr/bin/env bash
# =============================================================
# pre-commit-quality.sh — git pre-commit gate. Blocks committed secrets and
# oversized files before they reach history (docs/harness-profile.md, step 3).
# Every check has a `# pragma: allowlist secret` escape hatch so a false positive
# is one comment away, not a config war.
# Exit: 0 = PASS, 1 = BLOCK.
# Portability: POSIX ERE ([[:space:]] not \s; literal quotes not \x27). BSD/GNU safe.
# =============================================================

STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null)
[ -z "$STAGED_FILES" ] && exit 0

# --- Check 1: scan staged content for hardcoded secrets ---
# Coverage: AWS access key (AKIA…), AWS secret in assignment, PEM private-key blocks,
# OpenAI/Anthropic (sk-…, boundary-anchored + 20+ non-hyphen tail so kebab slugs don't
# match), Stripe (sk_live_/sk_test_/rk_live_/whsec_), Supabase (sb_secret_…), GitHub PAT
# (ghp_/gho_/ghs_/github_pat_), Google API (AIza…), Slack (xox[baprs]-…), and a generic
# password/api_key/secret/token = "…" assignment.
SECRET_PATTERNS='(AKIA[0-9A-Z]{16}|(^|[^A-Za-z0-9_-])sk-[A-Za-z0-9_-]*[A-Za-z0-9_]{20,}|sk_live_[a-zA-Z0-9]{20,}|sk_test_[a-zA-Z0-9]{20,}|rk_live_[a-zA-Z0-9]{20,}|whsec_[a-zA-Z0-9]{20,}|sb_secret_[a-zA-Z0-9_-]{20,}|ghp_[a-zA-Z0-9]{36}|gho_[a-zA-Z0-9]{36}|ghs_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{50,}|AIza[0-9A-Za-z_-]{35}|xox[baprs]-[0-9a-zA-Z-]{10,}|aws[_-]?(secret|access)[_A-Za-z]*[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9/+]{40}|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|(password|passwd|api[_-]?key|secret|token)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{8,})'

for FILE in $STAGED_FILES; do
  # skip binaries
  if echo "$FILE" | grep -qE '\.(png|jpg|jpeg|gif|ico|woff|woff2|ttf|eot|pdf|zip)$'; then continue; fi
  # skip .env.example (holds key names, not values)
  if echo "$FILE" | grep -qE '\.env\.example$'; then continue; fi

  RAW_MATCHES=$(git show ":$FILE" 2>/dev/null | grep -nEi "$SECRET_PATTERNS")
  [ -z "$RAW_MATCHES" ] && continue

  # drop lines carrying the allowlist pragma (test fixtures, doc examples)
  REAL_MATCHES=""
  while IFS= read -r match_line; do
    [ -z "$match_line" ] && continue
    if printf '%s' "$match_line" | grep -qF 'pragma: allowlist secret'; then continue; fi
    if [ -z "$REAL_MATCHES" ]; then REAL_MATCHES="$match_line"; else REAL_MATCHES="$REAL_MATCHES
$match_line"; fi
  done <<EOF
$RAW_MATCHES
EOF

  if [ -n "$REAL_MATCHES" ]; then
    echo "BLOCKED: Potential secret detected in staged file: $FILE" >&2
    printf '%s\n' "$REAL_MATCHES" | head -3 >&2
    echo "Action: Remove the secret; use environment variables / a secrets manager." >&2
    echo "        If this is a test fixture or doc example, add '# pragma: allowlist secret' on the line." >&2
    exit 1
  fi
done

# --- Check 2: block large staged files (>1MB) ---
for FILE in $STAGED_FILES; do
  if [ -f "$FILE" ]; then
    FILE_SIZE=$(wc -c < "$FILE" 2>/dev/null || echo 0)
    if [ "$FILE_SIZE" -gt 1048576 ]; then
      echo "BLOCKED: Large file staged ($(( FILE_SIZE / 1024 ))KB): $FILE" >&2
      echo "Action: Add to .gitignore or use Git LFS for large files." >&2
      exit 1
    fi
  fi
done

# --- Check 3: block committing .env files (allow .env.example) ---
for FILE in $STAGED_FILES; do
  BASENAME=$(basename "$FILE")
  if echo "$BASENAME" | grep -qE '^\.env($|\.[^e])'; then
    if ! echo "$BASENAME" | grep -qE '\.example$'; then
      echo "BLOCKED: .env file staged for commit: $FILE" >&2
      echo "Action: git reset HEAD $FILE && echo '$FILE' >> .gitignore" >&2
      exit 1
    fi
  fi
done

exit 0
