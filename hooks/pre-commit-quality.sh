#!/bin/bash
# =============================================================
# pre-commit-quality.sh — PreCommit hook
# Quality gate: checks staged files for secrets and issues
# Runs before every git commit (exit 1 = block commit)
# =============================================================

# --- Check 1: Scan staged files for potential secrets ---
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null)

if [ -z "$STAGED_FILES" ]; then
  # No staged files — nothing to check
  exit 0
fi

# Patterns that suggest hardcoded secrets
# BSD-grep compatible (POSIX ERE): uses [[:space:]] instead of \s, literal quotes instead of \x27
# Coverage:
#   - AWS access key (AKIA...)
#   - AWS secret key in assignment context (aws_secret/access_key = <40 base64>)
#   - PEM private key blocks (-----BEGIN ... PRIVATE KEY-----)
#   - OpenAI / Anthropic style (sk-...): anchored at a non-slug boundary and
#     requires a hyphen-free 20+ char tail so kebab-case slugs (e.g. a markdown
#     link ".../smb-security-human-riSK-x-neighborhood-watch.md") do NOT match.
#   - Stripe secret / restricted / webhook signing (sk_live_/sk_test_/rk_live_/whsec_)
#   - Supabase service role (modern: sb_secret_*)
#   - GitHub PAT (ghp_/gho_/ghs_/github_pat_)
#   - Google API (AIza...)
#   - Slack token (xox[baprs]-...)
#   - Generic password/api_key/secret/token in code (with quotes)
SECRET_PATTERNS='(AKIA[0-9A-Z]{16}|(^|[^A-Za-z0-9_-])sk-[A-Za-z0-9_-]*[A-Za-z0-9_]{20,}|sk_live_[a-zA-Z0-9]{20,}|sk_test_[a-zA-Z0-9]{20,}|rk_live_[a-zA-Z0-9]{20,}|whsec_[a-zA-Z0-9]{20,}|sb_secret_[a-zA-Z0-9_-]{20,}|ghp_[a-zA-Z0-9]{36}|gho_[a-zA-Z0-9]{36}|ghs_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{50,}|AIza[0-9A-Za-z_-]{35}|xox[baprs]-[0-9a-zA-Z-]{10,}|aws[_-]?(secret|access)[_A-Za-z]*[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9/+]{40}|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|(password|passwd|api[_-]?key|secret|token)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{8,})'

for FILE in $STAGED_FILES; do
  # Skip binary files and known safe patterns
  if echo "$FILE" | grep -qE '\.(png|jpg|jpeg|gif|ico|woff|woff2|ttf|eot|pdf|zip)$'; then
    continue
  fi

  # Skip .env.example (it's meant to hold key names, not values)
  if echo "$FILE" | grep -qE '\.env\.example$'; then
    continue
  fi

  # Check file content in the staged version
  RAW_MATCHES=$(git show ":$FILE" 2>/dev/null | grep -nEi "$SECRET_PATTERNS")
  if [ -z "$RAW_MATCHES" ]; then
    continue
  fi

  # Filter out lines with allowlist pragma marker
  # Convention: "# pragma: allowlist secret" on the same line bypasses the check.
  # Used for test fixtures, documentation examples, etc.
  REAL_MATCHES=""
  while IFS= read -r match_line; do
    [ -z "$match_line" ] && continue
    # match_line format: "LINE_NUM:CONTENT"
    if printf '%s' "$match_line" | grep -qF 'pragma: allowlist secret'; then
      continue  # allowlisted
    fi
    if [ -z "$REAL_MATCHES" ]; then
      REAL_MATCHES="$match_line"
    else
      REAL_MATCHES="$REAL_MATCHES
$match_line"
    fi
  done <<EOF
$RAW_MATCHES
EOF

  if [ -n "$REAL_MATCHES" ]; then
    echo "BLOCKED: Potential secret detected in staged file: $FILE" >&2
    printf '%s\n' "$REAL_MATCHES" | head -3 >&2
    echo "Action: Remove the secret and use environment variables / Secrets Manager." >&2
    echo "        If this is a test fixture or doc example, add '# pragma: allowlist secret' on the line." >&2
    exit 1
  fi
done

# --- Check 2: Warn about large staged files (>1MB) ---
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

# --- Check 3: Prevent committing .env files ---
for FILE in $STAGED_FILES; do
  BASENAME=$(basename "$FILE")
  if echo "$BASENAME" | grep -qE '^\.env($|\.[^e])'; then
    # Allow .env.example but block .env, .env.local, .env.production, etc.
    if ! echo "$BASENAME" | grep -qE '\.example$'; then
      echo "BLOCKED: .env file staged for commit: $FILE" >&2
      echo "Action: git reset HEAD $FILE && echo '$FILE' >> .gitignore" >&2
      exit 1
    fi
  fi
done

# All checks passed
exit 0
