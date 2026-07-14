#!/usr/bin/env bash
# Hermetic self-test for scripts/pulse-precheck.sh — the /pulse Verifier.
#
# The whole value of /pulse rests on ONE invariant: a probe that FAILS must render
# "❓ unknown", NEVER a false "0" (the "empty output != zero" rule). This test proves
# that offline, with fixture git repos + a fake `gh` that exits non-zero + file:// URLs,
# so it needs no network and is deterministic on macOS (BSD) and Linux (GNU).
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SCRIPT="$KIT_ROOT/scripts/pulse-precheck.sh"

assert_file_exists "$SCRIPT"
assert_cmd_ok "bash -n '$SCRIPT'" "pulse-precheck.sh is syntactically valid bash"

# BSD-grep portability: the script itself must not use grep -P or Perl \s (hooks-guide §3).
# Exclude comment lines so a doc line that literally says "no grep -P" isn't a false hit.
if grep -vE '^[[:space:]]*#' "$SCRIPT" | grep -nE 'grep[[:space:]]+(-[a-zA-Z]+[[:space:]]+)*-[a-zA-Z]*P' >/dev/null 2>&1; then
  _t_bad "pulse-precheck.sh uses non-portable 'grep -P'"  # pragma: allowlist bsd-grep
else
  _t_ok "pulse-precheck.sh has no 'grep -P' in code"  # pragma: allowlist bsd-grep
fi

# assert_no_match <string> <ERE> [msg] — inverse of assert_match (lib.sh has no negative form)
assert_no_match() {
  if printf '%s' "$1" | grep -qE -- "$2" 2>/dev/null; then _t_bad "${3:-/$2/ UNEXPECTEDLY present}"; else _t_ok "${3:-/$2/ correctly absent}"; fi
}

TMP="$(mktemp -d 2>/dev/null || mktemp -d -t pulse)"
trap 'rm -rf "$TMP"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

# ---- fixture A: github-host repo + fake failing gh + CI dir --------------------------
# origin is a LOCAL bare repo whose PATH contains "github.com" so host-detection fires
# offline; a fake `gh` on PATH exits 1 so every gh probe must degrade to ❓ (not 0).
BARE="$TMP/origin.github.com.git"
git init -q --bare "$BARE"
REPOA="$TMP/repoA"
git init -q "$REPOA"
( cd "$REPOA"
  git checkout -q -b main
  mkdir -p .github/workflows
  printf 'name: ci\n' > .github/workflows/ci.yml
  printf 'hello, no debt markers here\n' > file.txt
  git add -A && git commit -qm init
  git remote add origin "$BARE"
  git push -q origin main 2>/dev/null )
FAKEBIN="$TMP/fakebin"
mkdir -p "$FAKEBIN"
printf '#!/bin/sh\nexit 1\n' > "$FAKEBIN/gh"
chmod +x "$FAKEBIN/gh"

outA="$( cd "$REPOA" && PATH="$FAKEBIN:$PATH" bash "$SCRIPT" 2>/dev/null )"
prA="$(printf '%s\n' "$outA" | grep -E '^\[pr\]')"
ciA="$(printf '%s\n' "$outA" | grep -E '^\[ci\]')"
assert_match    "$prA" '❓'                  "[pr] renders ❓ when gh fails"
assert_no_match "$prA" 'open PRs: 0'         "[pr] never reports a false 'open PRs: 0' on gh failure"
assert_match    "$ciA" '❓'                  "[ci] renders ❓ when gh run list fails"
assert_no_match "$ciA" '🟢'                 "[ci] no green verdict invented on gh failure"

# ---- fixture B: no remote, clean tree ------------------------------------------------
REPOB="$TMP/repoB"
git init -q "$REPOB"
( cd "$REPOB" && git checkout -q -b work && printf 'x\n' > a.txt && git add -A && git commit -qm init )
outB="$( cd "$REPOB" && bash "$SCRIPT" 2>/dev/null )"
gitB="$(printf '%s\n'      "$outB" | grep -E '^\[git\]')"
brB="$(printf '%s\n'       "$outB" | grep -E '^\[branches\]')"
wipB="$(printf '%s\n'      "$outB" | grep -E '^\[wip\]')"
prB="$(printf '%s\n'       "$outB" | grep -E '^\[pr\]')"
assert_match    "$gitB" '❓'          "[git] ❓ when no upstream (not 'ahead 0/behind 0')"
assert_no_match "$gitB" 'ahead 0'     "[git] never invents 'ahead 0' with no tracking branch"
assert_match    "$brB"  '❓'          "[branches] ❓ when default branch unresolved (not assuming main)"
assert_match    "$wipB" '🟢'         "[wip] 🟢 clean tree"
assert_match    "$prB"  'no PR host'  "[pr] skips cleanly with no remote"

# ---- fixture C: dirty tree -----------------------------------------------------------
( cd "$REPOB" && printf 'untracked\n' > b.txt )
outC="$( cd "$REPOB" && bash "$SCRIPT" 2>/dev/null )"
wipC="$(printf '%s\n' "$outC" | grep -E '^\[wip\]')"
assert_match "$wipC" '🟡'            "[wip] 🟡 detects untracked WIP"

# ---- not a git repo ------------------------------------------------------------------
NOGIT="$TMP/plain"; mkdir -p "$NOGIT"
outN="$( cd "$NOGIT" && bash "$SCRIPT" 2>/dev/null )"
assert_match "$outN" '\[repo\] ❓'   "[repo] ❓ outside a git work tree"

# ---- prod fingerprint (green-while-broken) via file:// -------------------------------
if command -v curl >/dev/null 2>&1 && curl -fsSL "file://$TMP/repoB/a.txt" >/dev/null 2>&1; then
  GOOD="$TMP/prod-good.html"; printf '<html>WELCOME_MARKER ok</html>\n' > "$GOOD"
  # present -> 🟢
  outP="$( cd "$REPOB" && PULSE_PROD_CHECKS="file://$GOOD|WELCOME_MARKER" bash "$SCRIPT" 2>/dev/null )"
  assert_match "$(printf '%s\n' "$outP" | grep -E '^\[prod\]')" '🟢' "[prod] 🟢 when fingerprint present"
  # missing -> 🔴 (HTTP ok but content wrong)
  outP="$( cd "$REPOB" && PULSE_PROD_CHECKS="file://$GOOD|ABSENT_MARKER" bash "$SCRIPT" 2>/dev/null )"
  assert_match "$(printf '%s\n' "$outP" | grep -E '^\[prod\]')" '🔴' "[prod] 🔴 green-while-broken when fingerprint missing"
  # fetch fail -> ❓ (never a false green)
  outP="$( cd "$REPOB" && PULSE_PROD_CHECKS="file://$TMP/does-not-exist|X" bash "$SCRIPT" 2>/dev/null )"
  prodF="$(printf '%s\n' "$outP" | grep -E '^\[prod\]')"
  assert_match    "$prodF" '❓'    "[prod] ❓ on fetch failure"
  assert_no_match "$prodF" '🟢'   "[prod] never green on fetch failure"
else
  printf '  NOTE curl / file:// unavailable — skipped prod-fingerprint assertions\n'
fi

t_summary
