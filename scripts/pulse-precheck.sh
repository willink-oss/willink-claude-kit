#!/usr/bin/env bash
# pulse-precheck.sh — read-only live-state Verifier for /pulse (willink-claude-kit).
#
# Mutates NOTHING (git fetch only touches remote-tracking refs; no push/delete, no
# working-tree change, no PR/deploy mutation). Its stdout is the SOLE ground truth for
# the /pulse report — the model renders these lines, it does not invent status.
#
# Design invariants:
#   1. READ-ONLY.
#   2. FAIL-TO-UNKNOWN. Every probe: run -> check rc/stderr -> retry once -> on failure
#      emit "❓ unknown", NEVER a false "0". ("empty output != zero".)
#   3. Every run is timestamped.
#   4. Zero-config by default. `.claude/pulse.conf` (a shell file you own) and env vars
#      are optional; with neither, origin + stack detection still yields the core probes.
#   5. Idempotent & cheap. test / audit / prod-fetch probes are cost-gated (default skip).
#   6. Portable. POSIX ERE via `grep -E` only — no `grep -P`, no `\s`; use [[:space:]].
#      BSD/macOS + GNU/Linux safe. `date -j -f` (BSD) then `date -d` (GNU). External CLIs
#      are treated as single points of failure and degraded, never assumed present.
#
# No `set -e`/`-u`/`pipefail`: every probe must fail independently and degrade to
# "❓ unknown" rather than abort the whole script or report a false 0.

emit() { printf '%s\n' "$*"; }
now()  { date -u +"%Y-%m-%d %H:%M UTC"; }

# retry_argv <cmd...> : run argv, retry once, echo stdout, return last rc.
retry_argv() {
  local out rc
  out=$("$@" 2>/dev/null); rc=$?
  if [ "$rc" -ne 0 ]; then out=$("$@" 2>/dev/null); rc=$?; fi
  printf '%s' "$out"; return "$rc"
}
# retry_sh <string> : run a shell command string (for config-supplied cmds), retry once.
retry_sh() {
  local out rc
  out=$(bash -c "$1" 2>/dev/null); rc=$?
  if [ "$rc" -ne 0 ]; then out=$(bash -c "$1" 2>/dev/null); rc=$?; fi
  printf '%s' "$out"; return "$rc"
}

TS="$(now)"

# --- must be a git repo -----------------------------------------------------
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  emit "=== /pulse live-state — measured $TS ==="
  emit "[repo] ❓ not a git work tree — nothing to measure"
  exit 0
fi

# --- optional zero-config config (a shell file you own; env vars also honored) -
[ -f .claude/pulse.conf ] && . .claude/pulse.conf 2>/dev/null

# --- stack + host + CI detection preamble -----------------------------------
STACK="generic"; PKG=""
if   [ -f package.json ]; then STACK="node"
  if   [ -f pnpm-lock.yaml ]; then PKG="pnpm"
  elif [ -f yarn.lock ];      then PKG="yarn"
  elif [ -f bun.lockb ];      then PKG="bun"
  else PKG="npm"; fi
elif [ -f pubspec.yaml ];    then STACK="flutter"
elif [ -f go.mod ];          then STACK="go"
elif [ -f Cargo.toml ];      then STACK="rust"
elif [ -f composer.json ];   then STACK="php"
elif [ -f pyproject.toml ] || [ -f requirements.txt ]; then STACK="python"
fi

ORIGIN_URL=$(git remote get-url origin 2>/dev/null)
HOST="none"
case "$ORIGIN_URL" in
  *github.com*) command -v gh   >/dev/null 2>&1 && HOST="github" ;;
  *gitlab*)     command -v glab >/dev/null 2>&1 && HOST="gitlab" ;;
esac

CI="none"
[ -d .github/workflows ] && CI="gha"
[ -f .gitlab-ci.yml ]    && CI="gitlab"
[ -d .circleci ]         && CI="circle"

BR=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
HEAD_SHA=$(git rev-parse HEAD 2>/dev/null)

# Default branch — resolve for real (read-only), do NOT hardcode "main".
# After a fetch, refs/remotes/origin/HEAD is often unset locally, so fall back to
# asking the remote directly; if still unresolved, leave DEF empty and the branch
# probe emits ❓ rather than comparing against a wrong assumed default.
DEF=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')
if [ -z "$DEF" ]; then
  DEF=$(git ls-remote --symref origin HEAD 2>/dev/null | awk '/^ref:/{sub("refs/heads/","",$2); print $2; exit}')
fi

emit "=== /pulse live-state — measured $TS ==="
emit "stack=$STACK pkg=${PKG:-n/a} host=$HOST ci=$CI branch=${BR:-?} default=${DEF:-?}"

# --- Phase 1: sync (read-only: remote-tracking refs only) -------------------
if git fetch --all --prune --quiet 2>/dev/null; then :; else
  emit "[sync] ❓ fetch failed (offline?) — ahead/behind may be stale"
fi

# --- git position vs upstream -----------------------------------------------
if UP=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null); then
  if counts=$(git rev-list --left-right --count '@{upstream}...HEAD' 2>/dev/null); then
    behind=$(printf '%s' "$counts" | awk '{print $1}')
    ahead=$(printf '%s'  "$counts" | awk '{print $2}')
    sig="🟢"; [ "${ahead:-0}" -gt 0 ] && sig="🟡"; [ "${behind:-0}" -gt 0 ] && sig="🟡"
    emit "[git] $sig ahead ${ahead:-?} / behind ${behind:-?} vs $UP"
  else
    emit "[git] ❓ unknown — rev-list failed"
  fi
else
  emit "[git] ❓ no tracking branch (upstream unset) — ahead/behind unknown, NOT 0"
fi

# --- uncommitted / untracked WIP --------------------------------------------
if st=$(git status --porcelain=v1 2>/dev/null); then
  if [ -z "$st" ]; then
    emit "[wip] 🟢 working tree clean"
  else
    total=$(printf '%s\n' "$st"     | grep -cE '.')
    untracked=$(printf '%s\n' "$st" | grep -cE '^[?][?]')
    tracked=$(( total - untracked ))
    emit "[wip] 🟡 uncommitted: ${tracked} tracked / ${untracked} untracked (single-machine risk)"
  fi
else
  emit "[wip] ❓ unknown — git status failed"
fi

# --- open PRs + review state ------------------------------------------------
if [ "$HOST" = "github" ]; then
  open=$(retry_argv gh pr list --state open --json number --jq 'length'); rc=$?
  if [ "$rc" -ne 0 ]; then
    emit "[pr] ❓ unknown — gh failed (do NOT read as 0)"
  else
    rr=$(retry_argv gh pr list --state open --json reviewDecision --jq '[.[]|select(.reviewDecision=="REVIEW_REQUIRED")]|length')
    sig="🟢"; [ "${open:-0}" -gt 0 ] && sig="🟡"
    emit "[pr] $sig open PRs: ${open} / awaiting-review: ${rr:-?}"
  fi
elif [ "$HOST" = "gitlab" ]; then
  mrs=$(retry_argv glab mr list -P 100); rc=$?
  if [ "$rc" -ne 0 ]; then emit "[pr] ❓ unknown — glab failed"
  else n=$(printf '%s\n' "$mrs" | grep -cE '^!'); emit "[pr] 🟡 open MRs: ${n}"; fi
else
  emit "[pr] — no PR host detected (skip)"
fi

# --- latest CI run for current branch (matched to HEAD) ---------------------
if [ "$CI" = "gha" ] && [ "$HOST" = "github" ]; then
  run=$(retry_argv gh run list --branch "$BR" --limit 1 \
        --json headSha,status,conclusion \
        --jq 'if length==0 then "" else (.[0]|[.headSha,.status,.conclusion]|@tsv) end'); rc=$?
  if   [ "$rc" -ne 0 ]; then emit "[ci] ❓ unknown — gh run list failed (NOT 'no failures')"
  elif [ -z "$run" ];  then emit "[ci] — no CI runs for ${BR}"
  else
    rsha=$(printf '%s' "$run" | awk -F'\t' '{print $1}')
    rst=$(printf '%s'  "$run" | awk -F'\t' '{print $2}')
    rcc=$(printf '%s'  "$run" | awk -F'\t' '{print $3}')
    match="stale"; [ "$rsha" = "$HEAD_SHA" ] && match="HEAD"
    if   [ "$rst" != "completed" ]; then sig="🟡"; label="$rst"
    elif [ "$rcc" = "success" ];    then sig="🟢"; label="success"
    elif [ "$rcc" = "failure" ];    then sig="🔴"; label="failure"
    else sig="🟡"; label="${rcc:-$rst}"; fi
    emit "[ci] $sig ${label} (${match} commit)"
  fi
elif [ "$CI" = "none" ]; then emit "[ci] — no CI detected (skip)"
else emit "[ci] — CI=$CI probe not implemented (skip)"
fi

# --- latest tag vs HEAD (merged != deployed proxy) --------------------------
if tag=$(git describe --tags --abbrev=0 2>/dev/null); then
  if cnt=$(git rev-list "${tag}..HEAD" --count 2>/dev/null); then
    sig="🟢"; [ "${cnt:-0}" -gt 0 ] && sig="🟡"
    emit "[release] $sig ${cnt} commit(s) since ${tag} (unreleased/undeployed)"
  else emit "[release] ❓ unknown — rev-list failed"; fi
else emit "[release] — no tags yet (skip)"; fi

# --- cheap check (cost-gated; default SKIP) ---------------------------------
if [ -n "${PULSE_TEST_CMD:-}" ]; then cheap="$PULSE_TEST_CMD"
else
  case "$STACK" in
    node)
      # --no-install / plain exec so a status probe never triggers a network install.
      case "${PKG:-npm}" in
        npm) cheap="npm exec --no-install -- tsc --noEmit" ;;
        *)   cheap="${PKG} exec tsc -- --noEmit" ;;
      esac ;;
    flutter) cheap="flutter analyze" ;;
    go)      cheap="go vet ./..." ;;
    rust)    cheap="cargo check --quiet" ;;
    *)       cheap="" ;;
  esac
fi
if   [ -z "$cheap" ]; then emit "[check] — no cheap check for $STACK (skip)"
elif [ "${PULSE_RUN_TESTS:-0}" != "1" ]; then
  emit "[check] ⏭ not run (cost-gated) — set PULSE_RUN_TESTS=1 to run: ${cheap}"
else
  retry_sh "$cheap" >/dev/null; rc=$?
  if   [ "$rc" -eq 0 ];   then emit "[check] 🟢 ${cheap} passed"
  elif [ "$rc" -eq 127 ]; then emit "[check] ❓ tool not installed: ${cheap%% *}"
  else emit "[check] 🔴 ${cheap} failed (rc=$rc)"; fi
fi

# --- stale / merged remote branches (LIST ONLY; deletion is a later action) -
if [ -z "$DEF" ]; then
  emit "[branches] ❓ unknown — default branch unresolved (not assuming main)"
elif raw=$(git branch -r --merged "origin/${DEF}" 2>/dev/null); then
  merged=$(printf '%s\n' "$raw" \
           | grep -vE "origin/(HEAD|${DEF})([[:space:]]|$)" \
           | grep -cE 'origin/[^[:space:]]')
  if [ "${merged:-0}" -gt 0 ]; then
    emit "[branches] 🟡 ${merged} remote branch(es) merged into ${DEF} — deletable (list only)"
  else
    emit "[branches] 🟢 no stale merged remote branches"
  fi
else
  emit "[branches] ❓ unknown — branch enumeration failed"
fi

# --- TODO/FIXME density (git grep => respects .gitignore, skips vendor dirs) -
if raw=$(git grep -InE '(TODO|FIXME|HACK|XXX)' 2>/dev/null); then
  n=$(printf '%s\n' "$raw" | grep -cE '.')
  emit "[debt] ℹ ${n} TODO/FIXME/HACK/XXX marker(s)"
else
  emit "[debt] 🟢 no TODO/FIXME/HACK/XXX markers (or none tracked)"
fi

# --- dependency / security audit (cost + network gated; default SKIP) -------
# NOTE: opt-in. npm/yarn/etc. audit tools exit non-zero when they FIND issues
# (a documented exit code), which we render "reported issues"; rc=127 => not installed.
if [ "${PULSE_AUDIT:-0}" != "1" ]; then
  emit "[deps] ⏭ not run (cost-gated) — set PULSE_AUDIT=1 for security audit"
else
  case "$STACK" in
    node)
      if [ "$PKG" = "yarn" ] && yarn --version 2>/dev/null | grep -qE '^[2-9]'; then
        acmd="yarn npm audit --severity high"      # Yarn Berry (v2+) subcommand
      else
        acmd="${PKG:-npm} audit --audit-level=high"
      fi ;;
    python)  acmd="pip-audit" ;;
    go)      acmd="govulncheck ./..." ;;
    rust)    acmd="cargo audit" ;;
    php)     acmd="composer audit" ;;
    flutter) acmd="flutter pub outdated" ;;
    *)       acmd="" ;;
  esac
  if [ -z "$acmd" ]; then emit "[deps] — no audit tool for $STACK (skip)"
  else
    retry_sh "$acmd" >/dev/null; rc=$?
    if   [ "$rc" -eq 0 ];   then emit "[deps] 🟢 ${acmd} clean"
    elif [ "$rc" -eq 127 ]; then emit "[deps] ❓ audit tool not installed: ${acmd%% *}"
    else emit "[deps] 🟡 ${acmd} reported issues (rc=$rc)"; fi
  fi
fi

# --- prod content assertion (green-while-broken detector; config-driven) ----
# PULSE_PROD_CHECKS = newline-separated "url|expected_substring" pairs.
if [ -z "${PULSE_PROD_CHECKS:-}" ]; then
  emit "[prod] — no prod fingerprint configured (skip)"
else
  printf '%s\n' "$PULSE_PROD_CHECKS" | while IFS='|' read -r url expected; do
    [ -z "$url" ] && continue
    body=$(retry_argv curl -fsSL --max-time 10 "$url"); rc=$?
    if [ "$rc" -ne 0 ]; then emit "[prod] ❓ unknown — fetch failed: $url"; continue; fi
    if printf '%s' "$body" | grep -qF "$expected"; then
      emit "[prod] 🟢 ${url} — fingerprint present"
    else
      emit "[prod] 🔴 ${url} — HTTP ok but fingerprint MISSING (green-while-broken!)"
    fi
  done
fi

# --- state-doc freshness (freshness != truth) -------------------------------
DOCS_DEFAULT=$(printf 'README.md\nCHANGELOG.md')
printf '%s\n' "${PULSE_STATE_DOCS:-$DOCS_DEFAULT}" | while IFS= read -r d; do
  [ -z "$d" ] && continue
  [ -f "$d" ] || continue
  age=$(git log -1 --format=%cs -- "$d" 2>/dev/null)   # %cs = YYYY-MM-DD
  if [ -z "$age" ]; then emit "[doc] ❓ $d — no git history"; continue; fi
  now_s=$(date -u +%s)
  doc_s=$(date -j -f "%Y-%m-%d" "$age" +%s 2>/dev/null || date -d "$age" +%s 2>/dev/null)
  if [ -n "$doc_s" ]; then
    days=$(( (now_s - doc_s) / 86400 ))
    if [ "$days" -gt 30 ]; then emit "[doc] 🟡 $d last touched ${days}d ago (>30d — verify before trusting)"
    else emit "[doc] 🟢 $d fresh (${days}d)"; fi
  else
    emit "[doc] ❓ $d — date parse failed"
  fi
done

emit "=== end — every line is a probe result; ❓=unknown (NOT zero); '—'/⏭=skipped ==="
