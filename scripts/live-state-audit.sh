#!/usr/bin/env bash
# =============================================================
# live-state-audit.sh -- audit report-body state-claims against a preceding
#                        live probe (read-only).
#
# Purpose: scan a report / status / progress body and, for every state-claim
#   ("merged" / "deployed" / "published" / "released" / "done" ...), check that a
#   live-probe marker (gh / git / aws / a cloud CLI / curl / GitHub MCP) appears
#   BEFORE that claim in the SAME section. A claim with no preceding probe in its
#   section is an "unverified claim" and is flagged; one or more of them -> exit 1.
#   The principle: a document is *plan*; live is *state* -- no self-report.
#
# Boundaries (read-only, keep this true):
#   - This script audits (reads) only. It never edits files, never touches git,
#     deploy, secrets, or any external service. Detect != fix.
#   - Correcting a flag (re-measuring with a live probe, then fixing the wording)
#     is a human step. This engine only detects.
#   - It is the post-hoc counterpart to a pre-report advisory hook: that hook
#     nudges before you write; this gate machine-checks the body after you wrote it.
#
# Usage:
#   live-state-audit.sh --report <path>          # audit a file
#   live-state-audit.sh < report.md              # audit stdin
#   live-state-audit.sh --report <path> --json   # machine-readable (JSON) output
#   live-state-audit.sh --self-test              # gh/external-free, in-memory fixtures
#   live-state-audit.sh -h | --help              # print this header
#
# Exit codes:
#   0 = no unverified claims (no state-claim at all, OR every claim had a
#       preceding live probe in its section).
#   1 = >= 1 unverified claim (a state-claim with no preceding live-probe marker).
#   2 = argument error (e.g. --report file missing) OR a config error (a malformed
#       LSA_EXTRA_LIVE / LSA_EXTRA_CLAIMS regex) -- distinct from 1 so a wrapper never
#       mistakes a broken pattern for an "unverified claim" result.
#
# Deterministic --check:
#   bash scripts/live-state-audit.sh --self-test
#   -> runs several in-memory fixtures through the SAME python audit() the CLI uses
#      and asserts each exit code (probe-present -> 0 / probe-absent -> 1). All
#      fixtures agree -> exit 0 (PASS); any mismatch -> exit 1 (FAIL). No hardcoded
#      success -- the self-test and the CLI share one code path.
#
# Extension env vars (extend the patterns without editing code):
#   LSA_EXTRA_LIVE   -- if non-empty, "|" + its value is appended to LIVE_RE, so a
#                       team can register extra live-probe command signatures
#                       (e.g. "flyctl|heroku"). It is an ERE alternation fragment.
#   LSA_EXTRA_CLAIMS -- if non-empty, "|" + its value is appended to CLAIM_RE, so a
#                       non-English or domain-specific team can register extra
#                       state-claim phrases (e.g. "livre|entregado" or "\brolled\b").
#   LSA_JSON=1       -- emit machine-readable JSON instead of the human report
#                       (also set by --json).
#
# Design: text processing is python3 (standard library only -- no jq / gh
#   dependency in the engine). BSD/GNU-safe (no grep -P, no Perl escapes). The
#   self-test is hermetic (in-memory fixtures only; no external read/write; it
#   never touches a live probe).
# =============================================================
set -uo pipefail

# ---- audit engine (pure: stdin=report body -> tally unverified claims) --------
# For each state-claim phrase, it is "verified" if a live-probe marker appeared
# at or before its line WITHIN the same section, else "unverified". A blank line
# or a markdown heading resets the section so an opening probe cannot rubber-stamp
# the whole document -- evidence must be co-located with the claim. >=1 unverified
# -> exit 1. The CLI and --self-test both run this SAME code path (no hardcoded
# success).
# Read the engine into $PY via a quoted heredoc attached to `read` (NOT via
# $(cat <<..)): the command-substitution scanner miscounts a lone apostrophe in the
# heredoc body, so this pattern keeps the body fully literal regardless of prose.
IFS= read -r -d '' PY <<'PYEOF' || true
import sys, re, os, json

# Live-probe markers (command-signature based, so prose like "git repository" does
# not false-trigger). Extensible: if LSA_EXTRA_LIVE is set, append "|" + its value.
_live_pat = (
    r"gh\s+(pr|run|api|workflow|release|issue|repo|search)\b"
    r"|\bgit\s+(ls-files|log|rev-parse|show|status|branch|cat-file|diff)\b"
    r"|\baws\s+[a-z0-9-]+"
    r"|\b(kubectl|docker|terraform|gcloud|az|psql|redis-cli)\b"
    r"|\bcurl\b"
    r"|mcp__(plugin_)?github"
)
_extra_live = os.environ.get("LSA_EXTRA_LIVE", "")
if _extra_live:
    _live_pat = _live_pat + "|" + _extra_live

# State-claim phrases (volatile external / prod state that must be backed by a live
# probe). Extensible: if LSA_EXTRA_CLAIMS is set, append "|" + its value.
# Recall is favored over precision here: a MISSED claim is the dangerous mode (an
# unverified doc silently exits 0), so common deploy/merge/release synonyms are all
# included. Over-matching prose is fine -- flags are heuristic and a human triages.
_claim_pat = (
    r"\bmerged\b|\bdeployed\b|\bpublished\b|\breleased\b|\bshipped\b"
    r"|rolled out|rolled\s+\S+\s+out|in production|went to production|to production"
    r"|went live|we'?re live|\bis live\b|\bis up\b|up and running|pushed live|went out"
    r"|landed on (main|master)|deploy is green|prs?\s+(is|are)\s+in"
    r"|\bcompleted\b|\bdone\b|\bpassing\b"
)
_extra_claims = os.environ.get("LSA_EXTRA_CLAIMS", "")
if _extra_claims:
    _claim_pat = _claim_pat + "|" + _extra_claims

# Compile both patterns together. A malformed user-supplied LSA_EXTRA_* regex must
# degrade gracefully to a distinct config-error exit (2) -- NOT a raw traceback, and
# NOT exit 1 (which means "unverified claim") -- so a wrapper never mistakes a broken
# pattern for a clean or a blocked report. In --json mode we still emit valid JSON.
try:
    LIVE_RE = re.compile(_live_pat, re.IGNORECASE)
    CLAIM_RE = re.compile(_claim_pat, re.IGNORECASE)
except re.error as _e:
    _msg = "invalid regex in LSA_EXTRA_LIVE / LSA_EXTRA_CLAIMS: %s" % _e
    if os.environ.get("LSA_JSON") == "1":
        print(json.dumps({"error": "config", "detail": _msg}, ensure_ascii=False))
    else:
        sys.stderr.write("live-state-audit: %s\n" % _msg)
    sys.exit(2)

# Section / paragraph boundary (a markdown heading). Blank lines are handled
# inline. Both reset the evidence scope.
BOUNDARY_RE = re.compile(r"^\s*#{1,6}\s")
# A markdown list item (-, *, +, or "1."). Each item is its own evidence scope:
# status reports are bullet lists with NO blank line between items, so without a
# per-item reset one probe bullet would rubber-stamp every following claim bullet.
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


def audit(text):
    """Return (claims, unverified); each is a list of dicts."""
    live_seen = False
    claims = []
    unverified = []
    for i, line in enumerate(text.splitlines(), 1):
        # Reset live_seen at an evidence-scope boundary: a blank line, a heading,
        # OR the start of a new list item. Without this a single opening gh (or an
        # unrelated probe bullet) would verify the whole document / every following
        # bullet and hollow out the gate. The reset runs BEFORE the marker check
        # below, so a probe and a claim on the SAME bullet still counts as verified.
        if (line.strip() == "" or BOUNDARY_RE.match(line)
                or LIST_ITEM_RE.match(line)):
            live_seen = False
        # Evaluate the marker first, so "probe -> claim" on the same line/section
        # counts as verified.
        if LIVE_RE.search(line):
            live_seen = True
        for m in CLAIM_RE.finditer(line):
            rec = {
                "line": i,
                "phrase": m.group(0),
                "verified": live_seen,
                "snippet": line.strip()[:100],
            }
            claims.append(rec)
            if not live_seen:
                unverified.append(rec)
    return claims, unverified


def main():
    text = sys.stdin.read()
    claims, unverified = audit(text)
    if os.environ.get("LSA_JSON") == "1":
        print(json.dumps(
            {"claims": len(claims), "unverified": len(unverified), "flags": unverified},
            ensure_ascii=False,
        ))
    else:
        print("-- live-state audit (state-claim x preceding live probe) --")
        if not claims:
            print("  no state-claim phrase found (nothing to verify). exit 0.")
        else:
            for c in claims:
                mark = "OK  probe-first" if c["verified"] else "XX  unverified "
                print("  {} L{:>4} [{}] {}".format(mark, c["line"], c["phrase"], c["snippet"]))
            print("  -- total: {} claim(s) / {} unverified".format(len(claims), len(unverified)))
            if unverified:
                print("  ! unverified claim(s) -> add a live probe (gh/curl/aws/git) "
                      "BEFORE the claim in the same section.")
    sys.exit(1 if unverified else 0)


main()
PYEOF

run_audit() { python3 -c "$PY"; }

# ---- self-test: gh/external-free, in-memory fixtures verify the audit logic -----
self_test() {
  echo "-- live-state-audit self-test (external-free, audit-logic verification) --"
  fail=0

  # Each fixture is piped through the REAL audit code path ($PY) and its exit code
  # is compared to the expectation. No hardcoded success: the verdict is whatever
  # python's audit() actually returned this run.
  check_case() {
    label="$1"; expected="$2"; fixture="$3"
    printf '%s' "$fixture" | python3 -c "$PY" >/dev/null 2>&1
    rc=$?
    if [ "$rc" = "$expected" ]; then
      echo "  OK  ${label} (exit ${rc})"
    else
      echo "  XX  ${label} (exit ${rc} / expected ${expected})"
      fail=1
    fi
  }

  # A) probe present: gh pr view precedes "merged" -> verified -> exit 0
  fix_a=$'## PR #728\n`gh pr view 728 --json state,mergedAt` -> MERGED\nPR #728 is merged.'
  check_case "probe present (gh -> merged)" 0 "$fix_a"

  # B) probe absent: "deployed" claimed with no live marker -> exit 1
  fix_b=$'## Release\nDeployed to the service.'
  check_case "probe absent (deployed only)" 1 "$fix_b"

  # C) no claim phrase -> nothing to verify -> exit 0
  fix_c=$'## Notes\nReviewed the design approach today. Implementation starts tomorrow.'
  check_case "no claim phrase" 0 "$fix_c"

  # D) marker comes AFTER the claim (does not precede it) -> exit 1
  fix_d=$'Deployed to prod.\n`curl -s -o /dev/null -w "%{http_code}" https://example.com`'
  check_case "probe after claim (not preceding)" 1 "$fix_d"

  # E) mixed: gh verifies the first paragraph; a separate blank-line-separated
  #    paragraph's "up and running" has no probe -> unverified -> exit 1
  fix_e=$'`gh pr view 100 --json state` -> MERGED\nPR #100 is merged.\n\nSeparately, the service is up and running.'
  check_case "mixed (separate paragraph unverified)" 1 "$fix_e"

  # F) heading-separated: probe under one heading, claim under the next heading
  #    (the heading resets the evidence scope) -> exit 1
  fix_f=$'## Measured\n`gh pr view 728 --json state` -> MERGED\n## Conclusion\nPR #728 is merged.'
  check_case "heading-separated (scope reset by heading)" 1 "$fix_f"

  # G) curl precedes "up and running" -> verified -> exit 0
  fix_g=$'`curl -s -o /dev/null -w "%{http_code}" https://example.com` -> 200\nThe service is up and running.'
  check_case "probe present (curl -> up and running)" 0 "$fix_g"

  # H) a cloud CLI (kubectl) precedes "deployed" -> verified -> exit 0
  fix_h=$'`kubectl get deploy api` -> READY\nThe api is deployed to the CDN.'
  check_case "probe present (cloud CLI -> deployed)" 0 "$fix_h"

  # I) bullet list with NO blank line: one probe bullet must NOT rubber-stamp the
  #    following claim bullets (each list item is its own evidence scope) -> exit 1
  fix_i=$'## Status\n- `gh run list` shows CI is green\n- Feature X was deployed\n- The migration is merged'
  check_case "bullet list (probe bullet does not verify later claim bullets)" 1 "$fix_i"

  # J) probe AND claim on the SAME bullet -> still verified (no false positive from
  #    the per-item reset) -> exit 0
  fix_j=$'- `gh pr view 728 --json state` -> MERGED, so PR #728 is merged'
  check_case "same-bullet probe+claim (verified)" 0 "$fix_j"

  # K) a broadened claim phrasing with no probe is caught (recall over precision) -> exit 1
  fix_k=$'## Update\nThe new API went to production this morning.'
  check_case "broadened phrasing (went to production, no probe)" 1 "$fix_k"

  # L) a malformed LSA_EXTRA_LIVE regex degrades to a config error (exit 2), NOT a
  #    traceback and NOT exit 1 -- run through the same code path with the bad env set.
  bad_rc=0
  printf '%s' "$fix_b" | LSA_EXTRA_LIVE='[' python3 -c "$PY" >/dev/null 2>&1 || bad_rc=$?
  if [ "$bad_rc" = "2" ]; then
    echo "  OK  malformed LSA_EXTRA_LIVE -> config error (exit 2)"
  else
    echo "  XX  malformed LSA_EXTRA_LIVE (exit ${bad_rc} / expected 2)"
    fail=1
  fi

  if [ "$fail" -eq 0 ]; then
    echo "OK  SELF-TEST PASS (audit logic sound: probe-present -> exit0 / probe-absent -> exit1)"
    return 0
  fi
  echo "XX  SELF-TEST FAIL"
  return 1
}

# ---- argument parsing --------------------------------------------------------
REPORT=""; MODE="audit"
while [ $# -gt 0 ]; do
  case "$1" in
    --report) REPORT="${2:-}"; shift 2 ;;
    --json)   export LSA_JSON=1; shift ;;
    --self-test) MODE="self-test"; shift ;;
    -h|--help)
      grep -E '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ "$MODE" = "self-test" ]; then
  self_test
  exit $?
fi

# ---- audit mode (read only) --------------------------------------------------
if [ -n "$REPORT" ]; then
  if [ ! -f "$REPORT" ]; then
    echo "error: report not found: $REPORT" >&2
    exit 2
  fi
  run_audit < "$REPORT"
  exit $?
fi

# No --report given: read from stdin.
run_audit
exit $?
