#!/usr/bin/env bash
# =============================================================
# coverage-floor-check.sh — coverage floor lock (read-only guard)
#
# Purpose:
#   Read a coverage report (lcov / json / plain-text percentage) and:
#     (1) check that coverage % is at or above the floor (below-floor detection)
#     (2) check that the floor itself was not lowered vs a baseline (the previous
#         floor) — i.e. detect the anti-gaming case where someone quietly lowers
#         the floor so a dropping coverage number still "passes" (floor-lowering diff)
#
#   Either (1) below-floor OR (2) floor lowered → exit 1. Both clear → exit 0.
#
# Boundary (this is a read-only inspector):
#   This script only INSPECTS and PRINTS a verdict. It never changes the floor
#   value, CI config, or branch protection. Raising the floor / improving coverage
#   is done by hand; wiring a coverage floor into CI required checks or branch
#   protection is a high-risk (self-lockout) change that a human should apply.
#   It never calls git — the baseline is supplied by the caller via an argument.
#
# Usage:
#   coverage-floor-check.sh --coverage <file> --floor <N> [options]
#     --coverage <file>            path to the coverage report (required)
#     --format auto|lcov|json|text report format (default auto = by extension/content)
#     --floor <N>                  current floor threshold (%), a number
#     --floor-file <path>          read the current floor from a file (mutually exclusive with --floor)
#     --baseline-floor <M>         previous floor threshold (%). When set, current<previous → floor lowered = fail
#     --baseline-floor-file <path> read the previous floor from a file (optional)
#
#   coverage-floor-check.sh --self-test
#     No gh/aws/git dependency; hermetic (temp fixtures only).
#     Verifies at-or-above-floor→exit0 / below-floor→exit1 / floor-lowered→exit1 across all cases.
#     If any case disagrees with expectations it exits 1 (no self-reported success).
#
# exit code:
#   0 = coverage >= floor AND floor not lowered (pass)
#   1 = below floor (cov < floor) OR floor lowered (cur < baseline)
#   2 = missing/invalid arguments
#   3 = coverage / floor unparseable → state UNKNOWN (do not misread empty output as 0%)
#
# Design: parsing uses python3 (no jq dependency). Float comparison via awk. BSD grep safe (no grep -P).
# =============================================================
set -uo pipefail

# ---- pure function: coverage report → coverage % (float) -----------------
# args: <file> <format(auto|lcov|json|text)>
# output: decimal % string. "NaN" if unparseable.
parse_coverage() {
  python3 - "$1" "$2" <<'PY'
import sys, json, re
path = sys.argv[1]
fmt  = sys.argv[2]
try:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        data = f.read()
except Exception:
    print("NaN"); sys.exit(0)

def from_lcov(text):
    lf = 0; lh = 0; seen = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("LF:"):
            try: lf += int(s[3:]); seen = True
            except Exception: pass
        elif s.startswith("LH:"):
            try: lh += int(s[3:]); seen = True
            except Exception: pass
    if not seen or lf == 0:
        return None
    return lh * 100.0 / lf

def _dig(d, keys):
    cur = d
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    return cur

def from_json(text):
    try:
        d = json.loads(text)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    # istanbul/nyc coverage-summary.json: {"total":{"lines":{"pct":85.5}}}
    c = _dig(d, ["total", "lines", "pct"])
    if isinstance(c, (int, float)):
        return float(c)
    # top-level pct-like keys (0..1 treated as a ratio, x100)
    for k in ("pct", "coverage", "percent", "line_percent", "lines_pct"):
        v = d.get(k)
        if isinstance(v, (int, float)):
            v = float(v); return v * 100.0 if v <= 1.0 else v
    # cobertura line-rate (0..1)
    for k in ("line_rate", "line-rate", "lineRate"):
        v = d.get(k)
        if isinstance(v, (int, float)):
            return float(v) * 100.0
    return None

def from_text(text):
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*%', text)
    if m:
        return float(m.group(1))
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)', text)
    if m:
        v = float(m.group(1))
        return v * 100.0 if v <= 1.0 else v
    return None

if fmt == "auto":
    low = path.lower()
    if low.endswith(".info") or "end_of_record" in data or "\nLF:" in ("\n" + data) or "\nDA:" in ("\n" + data):
        fmt = "lcov"
    elif low.endswith(".json") or data.lstrip().startswith(("{", "[")):
        fmt = "json"
    else:
        fmt = "text"

val = None
if fmt == "lcov":
    val = from_lcov(data)
elif fmt == "json":
    val = from_json(data)
elif fmt == "text":
    val = from_text(data)

if val is None:
    print("NaN")
else:
    print("%.4f" % val)
PY
}

# ---- pure function: floor config file → floor value (float) --------------
# Accepts bare number / JSON ({"floor":N} etc.) / key:value or key=value.
# output: floor value, or "NaN" if unparseable.
parse_floor_file() {
  python3 - "$1" <<'PY'
import sys, re, json
path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        t = f.read()
except Exception:
    print("NaN"); sys.exit(0)
s = t.strip()
m = re.match(r'^([0-9]+(?:\.[0-9]+)?)$', s)
if m:
    print(m.group(1)); sys.exit(0)
try:
    d = json.loads(s)
    if isinstance(d, dict):
        for k in ("floor", "coverage_floor", "minimum", "min", "threshold"):
            if isinstance(d.get(k), (int, float)):
                print(d[k]); sys.exit(0)
except Exception:
    pass
m = re.search(r'(?:coverage_floor|floor|threshold|minimum|min)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)', s, re.I)
if m:
    print(m.group(1)); sys.exit(0)
print("NaN")
PY
}

# ---- pure functions: numeric test / float comparison (awk, BSD safe) ------
is_num() { printf '%s' "${1:-}" | awk '/^[0-9]+(\.[0-9]+)?$/{ok=1} END{exit !ok}'; }
# exit 0 if a >= b
ge() { awk -v a="$1" -v b="$2" 'BEGIN{exit !(a>=b)}'; }
# exit 0 if a <  b
lt() { awk -v a="$1" -v b="$2" 'BEGIN{exit !(a<b)}'; }

# ---- inspection body (one check; decides purely and returns an exit code) --
# args: coverage_file format cur_floor baseline_floor(may be empty)
# return: 0=pass / 1=below floor or floor lowered / 3=unparseable
run_check() {
  cov_file="$1"; fmt="$2"; cur_floor="$3"; base_floor="${4:-}"

  if ! is_num "$cur_floor"; then
    echo "  ❌ floor is not numeric: '${cur_floor}' → state UNKNOWN" >&2
    return 3
  fi

  cov=$(parse_coverage "$cov_file" "$fmt")
  if [ "$cov" = "NaN" ]; then
    echo "  ❌ coverage unparseable (${cov_file} / fmt=${fmt}) → state UNKNOWN" >&2
    echo "     do not misread empty/missing as 0% (empty output != zero)" >&2
    return 3
  fi

  fail=0

  # (1) below-floor check
  if ge "$cov" "$cur_floor"; then
    echo "  ✅ coverage ${cov}% >= floor ${cur_floor}% (threshold cleared)"
  else
    echo "  ❌ below floor: coverage ${cov}% < floor ${cur_floor}%"
    fail=1
  fi

  # (2) floor-lowering check (only when baseline is supplied)
  if [ -n "$base_floor" ]; then
    if ! is_num "$base_floor"; then
      echo "  ❌ baseline-floor is not numeric: '${base_floor}' → state UNKNOWN" >&2
      return 3
    fi
    if lt "$cur_floor" "$base_floor"; then
      echo "  ❌ floor lowered (floor-lowering diff detected): current floor ${cur_floor}% < baseline ${base_floor}%"
      echo "     note: lowering the floor is a gaming bypass. Apply changes by hand; wiring/altering CI gates or branch protection is a high-risk (self-lockout) change that needs human approval."
      fail=1
    else
      echo "  ✅ floor not lowered: current ${cur_floor}% >= baseline ${base_floor}%"
    fi
  fi

  return "$fail"
}

# ---- self-test: no gh/aws/git dependency; hermetic (temp fixtures only) ----
self_test() {
  echo "── coverage-floor-check self-test (hermetic, no external deps) ──"
  tmp="$(mktemp -d -t covfloor.XXXXXX)" || { echo "mktemp failed" >&2; return 1; }
  # always clean up
  trap 'rm -rf "$tmp"' RETURN 2>/dev/null || true

  # --- fixtures ---
  # lcov 90% (LF:10 LH:9)
  cat > "$tmp/high.info" <<'EOF'
TN:
SF:/src/a.js
DA:1,1
LF:10
LH:9
end_of_record
EOF
  # lcov 70% (LF:10 LH:7)
  cat > "$tmp/low.info" <<'EOF'
TN:
SF:/src/b.js
LF:10
LH:7
end_of_record
EOF
  # json istanbul summary 90%
  printf '%s\n' '{"total":{"lines":{"pct":90.0},"branches":{"pct":80}}}' > "$tmp/summary.json"
  # text ratio 0.90 (= 90%)
  printf '%s\n' 'Total coverage: 0.90' > "$tmp/ratio.txt"
  # text "90.0%" form
  printf '%s\n' 'Statements   : 90.0% ( 900/1000 )' > "$tmp/pct.txt"
  # unparseable (no numbers)
  printf '%s\n' 'no numbers here at all' > "$tmp/garbage.txt"

  fail=0
  # run a case: label / coverage_file / fmt / floor / baseline / expected exit
  run_case() {
    label="$1"; f="$2"; fmt="$3"; floor="$4"; base="$5"; want="$6"
    out=$(run_check "$f" "$fmt" "$floor" "$base" 2>&1); got=$?
    if [ "$got" = "$want" ]; then
      echo "  ✅ ${label} : exit=${got} (want ${want})"
    else
      echo "  ❌ ${label} : exit=${got} (want ${want})"
      printf '%s\n' "$out" | sed 's/^/       │ /'
      fail=1
    fi
  }

  # 1) at or above floor (lcov 90 >= 80, no baseline) → exit0
  run_case "lcov90>=floor80"          "$tmp/high.info"    auto 80 ""   0
  # 2) below floor (lcov 70 < 80) → exit1
  run_case "lcov70<floor80(below)"    "$tmp/low.info"     auto 80 ""   1
  # 3) floor lowered (cov 90 clears, but current floor 80 < baseline 85) → exit1
  run_case "floorlowered80<base85"    "$tmp/high.info"    auto 80 85   1
  # 4) json boundary (90 >= 90) → exit0
  run_case "json90>=floor90(bound)"   "$tmp/summary.json" auto 90 ""   0
  # 5) text ratio 0.90 → 90% >= 80 → exit0
  run_case "textratio0.90>=80"        "$tmp/ratio.txt"    auto 80 ""   0
  # 6) text "90.0%" >= 95 is below → exit1
  run_case "text90.0%<floor95(below)" "$tmp/pct.txt"      auto 95 ""   1
  # 7) unparseable → exit3 (UNKNOWN, not read as 0%)
  run_case "unparseable→UNKNOWN"      "$tmp/garbage.txt"  auto 80 ""   3
  # 8) floor not lowered AND pass (cov 90>=80, current 90>=base 80) → exit0
  run_case "floornotlowered+pass"     "$tmp/high.info"    auto 90 80   0
  # 9) floor lowered + simultaneously below floor (cov 70<80 and 80<85) → exit1
  run_case "below+floorlowered"       "$tmp/low.info"     auto 80 85   1

  # direct verification of the pure parser (parsed value match)
  assert_parse() {
    label="$1"; f="$2"; fmt="$3"; want="$4"
    got=$(parse_coverage "$f" "$fmt")
    if awk -v a="$got" -v b="$want" 'BEGIN{d=a-b; if(d<0)d=-d; exit !(d<0.01)}' 2>/dev/null; then
      echo "  ✅ parse:$label = ${got}% (want ${want})"
    else
      echo "  ❌ parse:$label = ${got} (want ${want})"
      fail=1
    fi
  }
  assert_parse "lcov"  "$tmp/high.info"    lcov 90.0
  assert_parse "json"  "$tmp/summary.json" json 90.0
  assert_parse "text%" "$tmp/pct.txt"      text 90.0

  # floor-file extraction checks
  printf '%s\n' '80' > "$tmp/floor_bare"
  printf '%s\n' '{"coverage_floor": 82.5}' > "$tmp/floor.json"
  printf '%s\n' 'floor = 77' > "$tmp/floor_kv"
  assert_floor() {
    label="$1"; f="$2"; want="$3"
    got=$(parse_floor_file "$f")
    if [ "$got" = "$want" ]; then
      echo "  ✅ floor-file:${label} = ${got}"
    else
      echo "  ❌ floor-file:${label} = ${got} (want ${want})"; fail=1
    fi
  }
  assert_floor "bare"  "$tmp/floor_bare" 80
  assert_floor "json"  "$tmp/floor.json" 82.5
  assert_floor "kv"    "$tmp/floor_kv"   77

  rm -rf "$tmp"; trap - RETURN 2>/dev/null || true
  if [ "$fail" -eq 0 ]; then
    echo "✅ SELF-TEST PASS (below-floor / floor-lowered / per-format parsing all agree)"
    return 0
  fi
  echo "❌ SELF-TEST FAIL"
  return 1
}

# ---- argument parsing --------------------------------------------------
COVERAGE=""; FORMAT="auto"; FLOOR=""; FLOOR_FILE=""
BASE_FLOOR=""; BASE_FLOOR_FILE=""; MODE="check"
while [ $# -gt 0 ]; do
  case "$1" in
    --coverage)            COVERAGE="${2:-}"; shift 2 ;;
    --format)              FORMAT="${2:-}"; shift 2 ;;
    --floor)               FLOOR="${2:-}"; shift 2 ;;
    --floor-file)          FLOOR_FILE="${2:-}"; shift 2 ;;
    --baseline-floor)      BASE_FLOOR="${2:-}"; shift 2 ;;
    --baseline-floor-file) BASE_FLOOR_FILE="${2:-}"; shift 2 ;;
    --self-test)           MODE="self-test"; shift ;;
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

# ---- check mode --------------------------------------------------------
if [ -z "$COVERAGE" ]; then
  echo "usage: coverage-floor-check.sh --coverage <file> --floor <N> [--baseline-floor <M>]" >&2
  echo "       coverage-floor-check.sh --self-test" >&2
  exit 2
fi
if [ ! -f "$COVERAGE" ]; then
  echo "❌ coverage file does not exist: $COVERAGE → state UNKNOWN" >&2
  exit 3
fi

# resolve the floor value (--floor wins, else extract from --floor-file)
if [ -z "$FLOOR" ] && [ -n "$FLOOR_FILE" ]; then
  FLOOR=$(parse_floor_file "$FLOOR_FILE")
fi
if [ -z "$FLOOR" ] || [ "$FLOOR" = "NaN" ]; then
  echo "❌ floor missing/unparseable (pass --floor or --floor-file)" >&2
  exit 2
fi

# resolve the baseline floor (optional)
if [ -z "$BASE_FLOOR" ] && [ -n "$BASE_FLOOR_FILE" ]; then
  BASE_FLOOR=$(parse_floor_file "$BASE_FLOOR_FILE")
  if [ "$BASE_FLOOR" = "NaN" ]; then
    echo "❌ cannot parse baseline-floor-file: $BASE_FLOOR_FILE → state UNKNOWN" >&2
    exit 3
  fi
fi

echo "── coverage floor check: cov=${COVERAGE} fmt=${FORMAT} floor=${FLOOR}%${BASE_FLOOR:+ baseline=${BASE_FLOOR}%} (inspect only, no apply) ──"
run_check "$COVERAGE" "$FORMAT" "$FLOOR" "$BASE_FLOOR"
rc=$?
case "$rc" in
  0) echo "  → verdict: pass (floor lock held)"; exit 0 ;;
  1) echo "  → verdict: fail (below floor or floor lowered). Raise the floor / improve coverage by hand (CI gate changes need human approval)"; exit 1 ;;
  *) echo "  → verdict: unknown (unparseable; not treated as 0%)"; exit 3 ;;
esac
