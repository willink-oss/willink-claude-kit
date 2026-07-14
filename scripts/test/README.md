# kit regression / integrity test suite

`willink-claude-kit` is a **prompt / plugin kit**, not application code — there is no
runtime to unit-test. What *can* regress are the kit's **integrity invariants**: the
plugin manifests, the adapter sync across Claude / Codex / Antigravity, the agent
"no early victory" guard phrases, the repo structure, and the release version pins.
This suite locks those invariants so an edit that silently breaks one is caught.

## Run

```bash
bash scripts/test/run.sh        # runs every test_*.sh, exits non-zero on any failure
```

Runs locally (macOS / Linux), in CI (`.github/workflows/test.yml`), and in the
autonomous test-quality loop.

## Layout

| File | Locks |
|---|---|
| `lib.sh` | assertion helpers (`assert_file_exists` / `assert_contains` / `assert_grep` / `assert_match` / `assert_eq` / `assert_cmd_ok` / `t_summary`) |
| `run.sh` | runner — discovers and runs all `test_*.sh`, aggregates pass/fail |
| `test_sync.sh` | `check_sync.py --check` (adapter sync + plugin parity + release integrity) |
| `test_plugin_manifest.sh` | plugin / marketplace / codex manifests are valid JSON with required fields; version is semver |
| `test_agent_guards.sh` | 4 subagents exist + keep their critical guard phrases verbatim |
| `test_build_guards.sh` | `commands/build.md` keeps the failure-mode guards (early-victory / telephone-game / options-flooding / Generator-Verifier) verbatim |
| `test_structure.sh` | required files / dirs (command, canonical + adapter skills, agents, scaffold, docs) exist |
| `test_hooks.sh` | example hooks (`examples/hooks/`) exist, are valid bash, and pass their block + pass self-test (behavioral run gated on `jq`) |

## Add a test (the loop does this continuously)

1. Create `scripts/test/test_<invariant>.sh`.
2. `source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"` to get `$KIT_ROOT` + assert helpers.
3. Make assertions, end with `t_summary`.
4. `bash scripts/test/run.sh` must stay green on `main`.

```bash
#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
assert_contains "$KIT_ROOT/agents/dev-planner.md" "some invariant phrase" "dev-planner keeps X"
t_summary
```

## The autonomous test-quality loop

A scheduled loop (crew `loop-test-cycle` / `CYCLE-PROMPT-test-kit`) continuously:
1. runs this suite,
2. **improves coverage** — finds an unlocked invariant and adds one `test_*.sh` (Draft PR, PR-only),
3. **on failure** — root-causes it: a test bug is fixed; a *source* (prompt/plugin) bug is filed as a `loop:approved` issue for the dev loop to fix.

Regression coverage therefore grows monotonically. Keep tests **fast, deterministic,
and dependency-free** (bash + python3 + grep only).
