#!/usr/bin/env bash
# The install docs must never hand users a snippet that silently disables the kit.
#
# Regression being locked (docs/failure-modes.md #11): README.md and adoption-guide.md
# used to present `"willink-claude-kit@iwillink": ["2.2.0"]` as a drop-in "version pin"
# for the boolean `true`. Following that advice leaves the plugin shown as enabled in
# /plugin while loading NO commands, agents or skills — with no error output at all.
# One environment ran ~6 days that way before anyone noticed.
#
# These are docs assertions rather than code assertions on purpose: for a prompt/plugin
# kit the install snippet IS the interface, and a wrong snippet breaks every adopter.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

README="$KIT_ROOT/README.md"
GUIDE="$KIT_ROOT/docs/adoption-guide.md"
MODES="$KIT_ROOT/docs/failure-modes.md"
DOCTOR="$KIT_ROOT/scripts/check-kit-enabled.sh"

assert_file_exists "$README"
assert_file_exists "$GUIDE"
assert_file_exists "$MODES"

# --- 1. the enable form is the boolean, in every install snippet -------------
for f in "$README" "$GUIDE"; do
  assert_contains "$f" '"willink-claude-kit@iwillink": true' \
    "$(basename "$f"): 有効化例が boolean true"
done

# --- 2. the dangerous array assignment must never reappear -------------------
# Scoped to the install docs: those are the copy-paste surface, where the snippet is
# read as an instruction. failure-modes.md is deliberately excluded — see check 5,
# which requires the same string there *labelled NG* so the anti-pattern stays taught.
for f in "$README" "$GUIDE"; do
  assert_not_contains "$f" '"willink-claude-kit@iwillink": ["' \
    "$(basename "$f"): array 代入例が存在しない"
done

# --- 3. the anti-pattern is explicitly warned about --------------------------
for f in "$README" "$GUIDE"; do
  assert_contains "$f" 'array 単独' \
    "$(basename "$f"): array 単独形式への警告がある"
done

# --- 4. the actively harmful instruction stays deleted -----------------------
# "必ず pin する" told users to replace the working boolean with the broken array.
assert_not_contains "$GUIDE" '必ず pin' \
  "adoption-guide: 「必ず pin」の誤指示が復活していない"

# --- 5. the failure mode stays documented ------------------------------------
assert_contains "$MODES" '## 11. kit が silently 無効化される' \
  "failure-modes: #11 が存在する"
assert_contains "$MODES" 'source.ref' \
  "failure-modes: pin は marketplace ref で行う方針が記載されている"
# The anti-pattern must stay *shown* here (labelled NG), so readers can recognise it
# in their own settings.json — the inverse of check 2's rule for the install docs.
assert_contains "$MODES" '"willink-claude-kit@iwillink": ["' \
  "failure-modes: array 形式の NG 例が掲載されている"
assert_contains "$MODES" '// NG' \
  "failure-modes: NG ラベルが付いている"

# --- 6. the doctor referenced by the docs actually exists and parses ---------
assert_file_exists "$DOCTOR"
assert_cmd_ok "bash -n '$DOCTOR'" "check-kit-enabled.sh は bash 構文として妥当"
for f in "$README" "$GUIDE"; do
  assert_contains "$f" 'scripts/check-kit-enabled.sh' \
    "$(basename "$f"): doctor スクリプトへの導線がある"
done

t_summary
