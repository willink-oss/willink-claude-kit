#!/usr/bin/env bash
# The plugin / marketplace / codex manifests are valid JSON and carry the required
# fields. A broken manifest silently disables the kit in Claude Code, so this is a
# high-value regression to lock.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PLUGIN="$KIT_ROOT/.claude-plugin/plugin.json"
MARKET="$KIT_ROOT/.claude-plugin/marketplace.json"
CODEX="$KIT_ROOT/.codex-plugin/plugin.json"

assert_file_exists "$PLUGIN"
assert_file_exists "$MARKET"
assert_file_exists "$CODEX"

# valid JSON (python3 is always present in CI + dev)
assert_cmd_ok "python3 -c 'import json,sys; json.load(open(sys.argv[1]))' '$PLUGIN'"  "plugin.json is valid JSON"
assert_cmd_ok "python3 -c 'import json,sys; json.load(open(sys.argv[1]))' '$MARKET'"  "marketplace.json is valid JSON"
assert_cmd_ok "python3 -c 'import json,sys; json.load(open(sys.argv[1]))' '$CODEX'"   "codex plugin.json is valid JSON"

# required non-empty fields on the canonical plugin manifest
for field in name version description license; do
  assert_cmd_ok "python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d.get(\"$field\")' '$PLUGIN'" \
    "plugin.json has non-empty field: $field"
done

# version is semver, name matches the package
ver="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$PLUGIN" 2>/dev/null || true)"
assert_match "$ver" '^[0-9]+\.[0-9]+\.[0-9]+$' "plugin version is semver (got: '$ver')"

name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$PLUGIN" 2>/dev/null || true)"
assert_eq "$name" "willink-claude-kit" "plugin name == willink-claude-kit"

t_summary
