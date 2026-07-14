#!/usr/bin/env bash
# Locks the live-state-verify-guard gate: the read-only audit that flags a report-body
# state-claim ("merged"/"deployed"/"published"/"done"...) with no live probe preceding it in
# the same section. Its value is the deterministic detection contract (probe-before-claim in
# the same section -> verified; probe absent / after the claim / in a different paragraph or
# heading -> unverified, exit 1) -- verified by its own hermetic --self-test plus end-to-end
# exit-code asserts here. If this rots, an unverified status-claim could pass an audit and a
# false "deployed"/"merged" reaches a report, so it is its own regression class.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

S="$KIT_ROOT/scripts"
LSA="$S/live-state-audit.sh"

# --- presence + valid bash + self-test ---
assert_file_exists "$LSA"
assert_cmd_ok "bash -n '$LSA'" "live-state-audit.sh is valid bash"
assert_cmd_ok "bash '$LSA' --self-test" "live-state-audit.sh --self-test passes"

# --- end-to-end exit-code contract on real fixtures (not just --self-test) ---
TMP="$(mktemp -d 2>/dev/null || mktemp -d -t lsa)"
trap 'rm -rf "$TMP"' EXIT

run_lsa() { bash "$LSA" --report "$1" >/dev/null 2>&1; printf '%s' "$?"; }

# probe precedes "merged" (same section) -> no unverified claim -> exit 0
cat > "$TMP/ok_merged.md" <<'M'
## PR #12
`gh pr view 12 --json state,mergedAt` -> MERGED
PR #12 is merged.
M
assert_eq "$(run_lsa "$TMP/ok_merged.md")" "0" "probe-precedes-merged -> exit 0"

# "deployed" with no probe anywhere -> unverified claim -> exit 1
cat > "$TMP/deploy_no_probe.md" <<'M'
## Release
Deployed to the service.
M
assert_eq "$(run_lsa "$TMP/deploy_no_probe.md")" "1" "deployed-no-probe -> exit 1"

# no state-claim phrase at all -> nothing to verify -> exit 0
cat > "$TMP/no_claim.md" <<'M'
## Notes
Reviewed the design approach today. Implementation starts tomorrow.
M
assert_eq "$(run_lsa "$TMP/no_claim.md")" "0" "no-claim -> exit 0"

# probe under one heading, claim under the next heading (heading resets scope) -> exit 1
cat > "$TMP/heading_split.md" <<'M'
## Measured
`gh pr view 728 --json state` -> MERGED
## Conclusion
PR #728 is merged.
M
assert_eq "$(run_lsa "$TMP/heading_split.md")" "1" "heading-separated probe/claim -> exit 1"

# a missing --report file is an argument error -> exit 2 (locks the arg-error contract)
assert_eq "$(run_lsa "$TMP/does_not_exist.md")" "2" "missing --report file -> exit 2"

# --- env extension: LSA_EXTRA_CLAIMS registers a claim word not in the base pattern ---
# "provisioned" is NOT a base CLAIM_RE phrase, and there is no live probe here.
cat > "$TMP/extra_claim.md" <<'M'
## Infra
The cluster is provisioned.
M
# without the env var, "provisioned" is not a claim -> nothing to verify -> exit 0
assert_eq "$(run_lsa "$TMP/extra_claim.md")" "0" "extra claim word NOT flagged without LSA_EXTRA_CLAIMS -> exit 0"
# with the env var, it becomes a claim and (no preceding probe) is flagged -> exit 1
extra_rc="$(LSA_EXTRA_CLAIMS='\bprovisioned\b' bash "$LSA" --report "$TMP/extra_claim.md" >/dev/null 2>&1; printf '%s' "$?")"
assert_eq "$extra_rc" "1" "extra claim word flagged WITH LSA_EXTRA_CLAIMS -> exit 1"

t_summary
