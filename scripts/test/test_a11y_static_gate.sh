#!/usr/bin/env bash
# Locks the a11y static gate. Two regression classes live here:
#
#   1. The gate's own truth table (its hermetic --self-test): name/role/state
#      detection per stack, the ratchet (baseline suppresses known, catches new),
#      the anti-gaming refusal to grow a baseline, and "zero files scanned =
#      UNKNOWN, not zero violations".
#   2. The false-positive fixtures found while validating against real repos
#      (implicit <label> wrapping, JSX inside template literals, monorepo lint
#      configs, PHP themes with a build package.json). A gate that cries wolf
#      gets switched off, so those stay locked too — they are part of --self-test.
#
# Also asserts the boundary the gate must never cross: it inspects and reports,
# it never edits code and never wires itself into CI.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

S="$KIT_ROOT/scripts"
GATE="$S/a11y-static-check.py"

assert_file_exists "$GATE"
assert_cmd_ok "python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' '$GATE'" \
  "a11y-static-check.py parses as valid python"
assert_cmd_ok "python3 '$GATE' --self-test" "a11y-static-check.py --self-test passes"

# Exit-code contract: a missing --root is a usage error (2), never a silent pass.
out="$(python3 "$GATE" 2>&1)"; rc=$?
assert_eq "$rc" "2" "no --root => exit 2 (usage error)"

# "Zero scannable files" must be UNKNOWN (3), not "clean" (0) — the empty-output-is-not-zero rule.
tmp="$(mktemp -d)"
python3 "$GATE" --root "$tmp" >/dev/null 2>&1; rc=$?
assert_eq "$rc" "3" "empty tree => exit 3 UNKNOWN (not 0)"

# A violation fails; the same tree with a baseline passes; a NEW violation fails again.
mkdir -p "$tmp/lib"
cat > "$tmp/lib/bad.dart" <<'DART'
class Bad extends StatelessWidget {
  @override
  Widget build(BuildContext context) =>
      GestureDetector(onTap: go, child: const Icon(Icons.book));
}
DART
python3 "$GATE" --root "$tmp" --no-project-checks >/dev/null 2>&1; rc=$?
assert_eq "$rc" "1" "unlabelled tap target => exit 1"

python3 "$GATE" --root "$tmp" --no-project-checks --baseline "$tmp/base.json" --update-baseline >/dev/null 2>&1
assert_file_exists "$tmp/base.json" "--update-baseline writes the snapshot"
python3 "$GATE" --root "$tmp" --no-project-checks --baseline "$tmp/base.json" >/dev/null 2>&1; rc=$?
assert_eq "$rc" "0" "baselined violation => exit 0 (ratchet)"

cat > "$tmp/lib/more.dart" <<'DART'
class More extends StatelessWidget {
  @override
  Widget build(BuildContext context) =>
      GestureDetector(onTap: go, child: const Icon(Icons.add));
}
DART
python3 "$GATE" --root "$tmp" --no-project-checks --baseline "$tmp/base.json" >/dev/null 2>&1; rc=$?
assert_eq "$rc" "1" "NEW violation on top of a baseline => exit 1"

# Anti-gaming: the baseline may shrink, never grow silently.
python3 "$GATE" --root "$tmp" --no-project-checks --baseline "$tmp/base.json" --update-baseline >/dev/null 2>&1; rc=$?
assert_eq "$rc" "1" "growing the baseline is refused without --allow-baseline-growth"
python3 "$GATE" --root "$tmp" --no-project-checks --baseline "$tmp/base.json" --update-baseline --allow-baseline-growth >/dev/null 2>&1; rc=$?
assert_eq "$rc" "0" "growth allowed only with the explicit flag"

# A referenced-but-missing baseline is an error (2), never "nothing known, nothing new".
python3 "$GATE" --root "$tmp" --no-project-checks --baseline "$tmp/nope.json" >/dev/null 2>&1; rc=$?
assert_eq "$rc" "2" "missing baseline => exit 2 (never fail-open)"

# json output stays machine-readable
python3 "$GATE" --root "$tmp" --no-project-checks --format json > "$tmp/out.json" 2>/dev/null
assert_cmd_ok "python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"findings\"] and d[\"stats\"][\"files_scanned\"]>0' '$tmp/out.json'" \
  "--format json emits findings + scan denominators"

rm -rf "$tmp"

# Boundary locks (verbatim): detection only, and it never self-installs into CI.
assert_contains "$GATE" "DETECTION ONLY" "gate documents that it never edits code"
assert_contains "$GATE" "Never installs itself into CI" "gate documents that CI wiring stays a human decision"
assert_not_contains "$GATE" "subprocess" "gate shells out to nothing (no gh/aws/git dependency)"

# The skill + adoption examples ship with it.
assert_file_exists "$KIT_ROOT/skills/a11y-static-gate/SKILL.md"
assert_file_exists "$KIT_ROOT/skills/a11y-standards/SKILL.md"
assert_file_exists "$KIT_ROOT/examples/a11y/a11y-baseline.example.json"
assert_file_exists "$KIT_ROOT/examples/a11y/flutter/a11y_smoke_test.dart"
assert_file_exists "$KIT_ROOT/examples/a11y/web/eslint-a11y.config.md"
assert_file_exists "$KIT_ROOT/examples/ci/a11y-gate-pattern.md"

t_summary
