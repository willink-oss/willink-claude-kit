#!/usr/bin/env bash
# =============================================================
# fanout-verify-synth.sh — the "may we synthesize?" stop primitive for
# fanned-out claim verification.
#
# In a deep-research / multi-agent fan-out, several agents each emit claims and
# each claim is verified adversarially. This script is NOT the part that "runs"
# the loop (spawning the fan-out, executing the verification) — it reads the
# collected verification result (verify.json) and decides, DETERMINISTICALLY,
# whether every claim is verified and therefore synthesis is allowed. The answer
# is returned as an exit code (same family as goal-loop.sh: a "stop / verify"
# primitive).
#
# The decision forbids self-report: a bare "verified" label is not enough. Only a
# claim that also carries at least --min-sources pieces of evidence is counted as
# effectively verified — a label is self-report, an evidence count is data.
#
# verify.json schema:
#   {
#     "topic": "...",
#     "claims": [
#       { "id": "c1", "claim": "...", "status": "verified|refuted|unverified|pending",
#         "sources": ["https://...", "..."], "agent": "agent-a" },
#       ...
#     ]
#   }
#
# exit code:
#   0 → ✅ ADOPT   : every claim is effectively verified. Synthesis is allowed.
#   1 → 🔁 HOLD    : unverified/pending/under-sourced present, no refutations.
#                    Do more fan-out / re-verify. (Zero claims is also HOLD — fail-safe.)
#   2 → 🛑 CONFLICT: a refuted claim is present. Do not synthesize; escalate to a human.
#   3 → usage / JSON parse error.
#
# Usage:
#   fanout-verify-synth.sh --in verify.json [--min-sources 1] [--json]
#   fanout-verify-synth.sh --self-test        # hermetic self-test (real behavior)
#
# Design:
#   - JSON parsing uses the python3 standard library (avoids a single-CLI
#     dependency; no jq required).
#   - Deterministic: only the exit code is truth. Empty / broken input fails
#     SAFE toward non-adoption.
#   - POSIX-ish shell + BSD/GNU-safe grep (no grep -P, no Perl escapes).
# =============================================================
set -uo pipefail

usage() {
  cat >&2 <<'EOF'
usage:
  fanout-verify-synth.sh --in <verify.json> [--min-sources N] [--json]
  fanout-verify-synth.sh --self-test
exit: 0=ADOPT 1=HOLD 2=CONFLICT 3=usage/parse-error
EOF
}

# ---- core: read verify.json and decide adoption deterministically -----------
# args: <in-file> <min-sources> <json:0|1>  / exit code = the verdict
decide() {
  IN="$1"; MIN="$2"; JSON="$3"
  [ -f "$IN" ] || { echo "fanout-verify-synth: not found: $IN" >&2; return 3; }
  FVS_IN="$IN" FVS_MIN="$MIN" FVS_JSON="$JSON" python3 - <<'PY'
import json, os, sys

path = os.environ["FVS_IN"]
try:
    min_sources = int(os.environ.get("FVS_MIN", "1"))
except ValueError:
    print("fanout-verify-synth: --min-sources must be an integer", file=sys.stderr)
    sys.exit(3)
as_json = os.environ.get("FVS_JSON", "0") == "1"

try:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
except (json.JSONDecodeError, ValueError) as e:
    print(f"fanout-verify-synth: invalid JSON in {path}: {e}", file=sys.stderr)
    sys.exit(3)

if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
    print("fanout-verify-synth: JSON must be an object with a 'claims' array", file=sys.stderr)
    sys.exit(3)

claims = data["claims"]
topic = data.get("topic", "<no-topic>")

verified, refuted, held = [], [], []
for i, c in enumerate(claims):
    if not isinstance(c, dict):
        held.append((f"#{i}", "malformed", "claim is not an object"))
        continue
    cid = str(c.get("id", f"#{i}"))
    status = str(c.get("status", "pending")).lower()
    srcs = c.get("sources", [])
    n = len(srcs) if isinstance(srcs, list) else 0
    if status == "refuted":
        refuted.append((cid, status, f"{n} src"))
    elif status == "verified" and n >= min_sources:
        verified.append((cid, status, f"{n} src"))
    elif status == "verified":
        # labeled verified but under-sourced -> demoted to held (a label is
        # self-report; evidence is required)
        held.append((cid, "verified*", f"{n}<{min_sources} src (demoted: insufficient evidence)"))
    else:
        held.append((cid, status, f"{n} src"))

total = len(claims)
# verdict
if total == 0:
    code, verdict = 1, "HOLD"       # zero claims is not adoption (fail-safe)
elif refuted:
    code, verdict = 2, "CONFLICT"
elif not held:
    code, verdict = 0, "ADOPT"
else:
    code, verdict = 1, "HOLD"

if as_json:
    out = {
        "verdict": verdict, "exit": code, "topic": topic,
        "total": total, "verified": len(verified),
        "refuted": len(refuted), "held": len(held),
        "min_sources": min_sources,
        "refuted_ids": [x[0] for x in refuted],
        "held_ids": [x[0] for x in held],
    }
    print(json.dumps(out, ensure_ascii=False))
else:
    sym = {0: "✅ ADOPT", 1: "🔁 HOLD", 2: "🛑 CONFLICT"}[code]
    print(f"{sym}  topic={topic}  verified={len(verified)}/{total} "
          f"refuted={len(refuted)} held={len(held)} (min-sources={min_sources})")
    for cid, st, why in refuted:
        print(f"   🛑 refuted : {cid} [{st}] {why}")
    for cid, st, why in held:
        print(f"   🔁 held    : {cid} [{st}] {why}")
    if code == 0:
        print("   → every claim is effectively verified. Synthesis is allowed")
    elif code == 1:
        print("   → unverified / under-sourced claims present. Do more fan-out or re-verify, then re-judge")
    else:
        print("   → refuted claim(s) present — do not synthesize; escalate to a human")

sys.exit(code)
PY
}

# ---- hermetic self-test ------------------------------------------------------
self_test() {
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/fvs.XXXXXX")" || { echo "mktemp failed" >&2; return 1; }
  trap 'rm -rf "$tmp"' EXIT
  fails=0
  total=0

  assert() { # <label> <expected-exit> <actual-exit>
    total=$((total + 1))
    if [ "$2" = "$3" ]; then
      echo "  ok   $1 (exit $3)"
    else
      echo "  FAIL $1 (expected $2, got $3)"; fails=$((fails + 1))
    fi
  }

  # 1) all verified + sourced → ADOPT (0)
  cat > "$tmp/all_verified.json" <<'J'
{ "topic": "t", "claims": [
  {"id":"c1","status":"verified","sources":["https://a"]},
  {"id":"c2","status":"verified","sources":["https://b","https://c"]}
]}
J
  decide "$tmp/all_verified.json" 1 0 >/dev/null 2>&1; assert "all-verified→ADOPT" 0 $?

  # 2) an unverified claim mixed in → HOLD (1)
  cat > "$tmp/mixed.json" <<'J'
{ "topic": "t", "claims": [
  {"id":"c1","status":"verified","sources":["https://a"]},
  {"id":"c2","status":"unverified","sources":[]}
]}
J
  decide "$tmp/mixed.json" 1 0 >/dev/null 2>&1; assert "unverified-mixed→HOLD" 1 $?

  # 3) a refuted claim mixed in → CONFLICT (2)
  cat > "$tmp/refuted.json" <<'J'
{ "topic": "t", "claims": [
  {"id":"c1","status":"verified","sources":["https://a"]},
  {"id":"c2","status":"refuted","sources":["https://x"]}
]}
J
  decide "$tmp/refuted.json" 1 0 >/dev/null 2>&1; assert "refuted→CONFLICT" 2 $?

  # 4) labeled verified but under-sourced → demoted to HOLD (1)  (no self-report)
  cat > "$tmp/nosource.json" <<'J'
{ "topic": "t", "claims": [
  {"id":"c1","status":"verified","sources":[]}
]}
J
  decide "$tmp/nosource.json" 1 0 >/dev/null 2>&1; assert "verified-but-no-source→HOLD" 1 $?

  # 4b) same input with --min-sources 0 → ADOPT (shows the threshold really bites)
  decide "$tmp/nosource.json" 0 0 >/dev/null 2>&1; assert "min-sources=0-relaxes→ADOPT" 0 $?

  # 5) empty claims → HOLD (1) fail-safe
  echo '{ "topic":"t", "claims": [] }' > "$tmp/empty.json"
  decide "$tmp/empty.json" 1 0 >/dev/null 2>&1; assert "empty-claims→HOLD" 1 $?

  # 6) broken JSON → parse error (3)
  echo '{ "claims": [ oops ' > "$tmp/broken.json"
  decide "$tmp/broken.json" 1 0 >/dev/null 2>&1; assert "broken-json→ERROR" 3 $?

  # 7) claims is not an array → parse error (3)
  echo '{ "claims": "nope" }' > "$tmp/notarray.json"
  decide "$tmp/notarray.json" 1 0 >/dev/null 2>&1; assert "claims-not-array→ERROR" 3 $?

  # 8) missing file → error (3)
  decide "$tmp/does-not-exist.json" 1 0 >/dev/null 2>&1; assert "missing-file→ERROR" 3 $?

  echo ""
  if [ "$fails" -eq 0 ]; then
    echo "✅ self-test PASSED ($total/$total)"
    return 0
  else
    echo "❌ self-test FAILED ($fails case(s))"
    return 1
  fi
}

# ---- arg parse ---------------------------------------------------------------
IN=""; MIN=1; JSON=0
[ $# -gt 0 ] || { usage; exit 3; }
while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) self_test; exit $? ;;
    --in)          [ $# -ge 2 ] || { echo "fanout-verify-synth: --in needs a value" >&2; usage; exit 3; }; IN="$2"; shift 2 ;;
    --min-sources) [ $# -ge 2 ] || { echo "fanout-verify-synth: --min-sources needs a value" >&2; usage; exit 3; }; MIN="$2"; shift 2 ;;
    --json)        JSON=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 3 ;;
  esac
done

[ -n "$IN" ] || { echo "fanout-verify-synth: --in <verify.json> is required" >&2; usage; exit 3; }
case "$MIN" in ''|*[!0-9]*) echo "fanout-verify-synth: --min-sources must be a non-negative integer" >&2; exit 3 ;; esac

decide "$IN" "$MIN" "$JSON"
exit $?
