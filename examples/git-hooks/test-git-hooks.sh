#!/usr/bin/env bash
# Self-test for the example git hooks — asserts BOTH a block and a pass case for each,
# using a throwaway git repo with staged fixtures (the hooks read `git show :file`).
# Self-contained: copy examples/git-hooks/ wholesale and `bash test-git-hooks.sh` works.
# Fixtures (fake secret / grep -P line) are built at RUNTIME so no literal secret or
# non-portable token sits in this file. Skips if git is unavailable.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUAL="$HERE/pre-commit-quality.sh"
LINT="$HERE/pre-commit-shell-lint.sh"

pass=0; fail=0
ok()  { pass=$((pass + 1)); printf '  PASS %s\n' "$1"; }
bad() { fail=$((fail + 1)); printf '  FAIL %s\n' "$1"; }

command -v git >/dev/null 2>&1 || { printf 'SKIP: git not installed.\n'; exit 0; }

TMP="$(mktemp -d 2>/dev/null || mktemp -d -t githooks)"
trap 'rm -rf "$TMP"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
REPO="$TMP/repo"; git init -q "$REPO"

run() { ( cd "$REPO" && bash "$1" ) >/dev/null 2>&1; printf '%s' "$?"; }
unstage_all() { ( cd "$REPO" && git reset -q >/dev/null 2>&1 ); }

# fixture secret built at runtime: AKIA + 16 digits (matches AKIA[0-9A-Z]{16}); never a
# literal key in this file.
FAKEKEY="AKIA$(printf '0%.0s' $(seq 1 16))"
# fixture "grep -P" token built in two steps so this file has no literal 'grep -P'.
GBAD="grep"; GBAD="$GBAD -P"

# --- pre-commit-quality.sh ----------------------------------------------------
unstage_all
printf 'const k = "%s"\n' "$FAKEKEY" > "$REPO/leak.txt"
( cd "$REPO" && git add leak.txt )
ec="$(run "$QUAL")"; [ "$ec" = "1" ] && ok "quality blocks a fake AWS key (exit 1)" || bad "quality should block secret (got $ec)"

unstage_all
( cd "$REPO" && rm -f leak.txt && printf 'hello world\n' > clean.txt && git add clean.txt )
ec="$(run "$QUAL")"; [ "$ec" = "0" ] && ok "quality allows a clean file (exit 0)" || bad "quality should pass clean (got $ec)"

unstage_all
( cd "$REPO" && printf 'API_TOKEN=abc\n' > .env && git add .env )
ec="$(run "$QUAL")"; [ "$ec" = "1" ] && ok "quality blocks a staged .env (exit 1)" || bad "quality should block .env (got $ec)"
( cd "$REPO" && git reset -q >/dev/null 2>&1 && rm -f .env clean.txt )

# --- pre-commit-shell-lint.sh -------------------------------------------------
unstage_all
printf '#!/bin/bash\n%s foo bar\n' "$GBAD" > "$REPO/x.sh"
( cd "$REPO" && git add x.sh )
ec="$(run "$LINT")"; [ "$ec" = "1" ] && ok "shell-lint blocks 'grep -P' in staged .sh (exit 1)" || bad "shell-lint should block grep -P (got $ec)"  # pragma: allowlist bsd-grep

unstage_all
( cd "$REPO" && git rm -q --cached x.sh >/dev/null 2>&1; rm -f x.sh )
printf '#!/bin/bash\ngrep -E "[[:space:]]+" foo\n' > "$REPO/y.sh"
( cd "$REPO" && git add y.sh )
ec="$(run "$LINT")"; [ "$ec" = "0" ] && ok "shell-lint allows BSD-safe grep -E (exit 0)" || bad "shell-lint should pass grep -E (got $ec)"

printf '  -> %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
