#!/usr/bin/env bash
# Locks the cogload-dashboard observation instrument. Its whole value rests on refusing to
# make "checked nothing" look like "found nothing", so the regression classes here are the
# honesty invariants rather than the rendering: an UNKNOWN probe must never render as 0,
# an empty scan must not report a confident zero, and a run that measured nothing at all
# must exit 3 rather than 0. It is an instrument, not a gate — a failing probe must not
# fail the run, which is why the exit-code truth table is asserted explicitly.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

S="$KIT_ROOT/scripts"
DASH="$S/cogload-dashboard.py"

assert_file_exists "$DASH"
assert_cmd_ok "python3 -m py_compile '$DASH'" "cogload-dashboard.py is valid python"
assert_cmd_ok "python3 '$DASH' --self-test" "cogload-dashboard.py --self-test passes"

# skill + annotated example config ship together; the example is the only documentation of
# the probe schema, so a probe type it names that the script does not implement is a defect.
assert_file_exists "$KIT_ROOT/skills/cogload-dashboard/SKILL.md"
assert_file_exists "$KIT_ROOT/examples/cogload/cogload.config.example.json"
assert_cmd_ok "python3 -c \"import json;json.load(open('$KIT_ROOT/examples/cogload/cogload.config.example.json'))\"" \
  "example config is valid JSON"
assert_cmd_ok "python3 - <<'PY'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('cl', '$DASH')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
cfg = json.load(open('$KIT_ROOT/examples/cogload/cogload.config.example.json'))
missing = [p['type'] for p in cfg['probes'] if p['type'] not in m.PROBE_TYPES]
sys.exit(1 if missing else 0)
PY" "every probe type in the example config is implemented"

# Exit-code truth table. 3 (measured nothing) must stay distinct from 0 (all clear):
# a caller that conflates them reports an unmonitored system as a healthy one.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/empty" "$TMP/out"

assert_cmd_ok "python3 '$DASH' --root '$KIT_ROOT' --out-dir '$TMP/out' --offline" \
  "a repo with a working git tree exits 0"
assert_file_exists "$TMP/out/dashboard.html"
assert_file_exists "$TMP/out/snapshot.json"

set +e
python3 "$DASH" --root "$TMP/empty" --out-dir "$TMP/out2" --offline >/dev/null 2>&1
rc_nothing=$?
python3 "$DASH" --config "$TMP/does-not-exist.json" --out-dir "$TMP/out3" >/dev/null 2>&1
rc_badcfg=$?
set -e
assert_eq "3" "$rc_nothing" "measuring nothing exits 3, not 0"
assert_eq "2" "$rc_badcfg" "a missing explicit config exits 2"

# The rendered page must be publishable as a self-contained artifact and must never
# present an unmeasured probe as a zero.
assert_not_contains "$TMP/out/dashboard.html" "<!DOCTYPE" "page is a fragment, not a full document"
assert_grep "$TMP/out/dashboard.html" 'aria-labelledby="lane-1"' "lanes are named regions"
assert_grep "$TMP/out2/dashboard.html" "unknown" "an unmeasurable probe renders as unknown"
assert_not_contains "$TMP/out2/dashboard.html" '<span class="num">0</span>' \
  "an unmeasurable probe never renders as 0"

t_summary
