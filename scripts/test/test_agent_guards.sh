#!/usr/bin/env bash
# The 4 subagents exist, carry frontmatter, and KEEP their critical "no early victory"
# guard phrases. These guards are what make the Generator-Verifier separation work; if
# a refactor silently drops them, the kit regresses to rubber-stamp reviews. Locked
# verbatim so any edit to the wording is a deliberate, reviewed change.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

AG="$KIT_ROOT/agents"

for a in dev-explorer dev-planner dev-reviewer dev-tester; do
  assert_file_exists "$AG/$a.md"
  assert_grep "$AG/$a.md" '^name:'        "$a.md has frontmatter name:"
  assert_grep "$AG/$a.md" '^description:' "$a.md has frontmatter description:"
done

# Critical guard phrases (verbatim regression locks).
assert_contains "$AG/dev-tester.md"   'Run the full test suite before marking as passed' \
  "dev-tester keeps the no-early-victory (full suite) guard"
assert_contains "$AG/dev-reviewer.md" 'Review the FULL diff before marking PASS' \
  "dev-reviewer keeps the full-diff guard"
assert_contains "$AG/dev-explorer.md" 'No nested subagents' \
  "dev-explorer keeps the no-nested-subagents guard"

t_summary
