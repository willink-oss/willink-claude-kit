---
name: commit-convention-gate
description: Deterministically check ONE commit message (arg / file / stdin) and reject it (exit 1) on a bad prefix, an empty phrase (a correct prefix followed by "update X"), or a missing WHY. No LLM — same input, same output. Wiring it as a commit-msg hook is a separate manual step. Triggers: commit convention, commit-msg check, reject empty commits, prefix check, missing why, conventional commits gate.
allowed-tools: Bash, Read
---

# commit-convention-gate

A deterministic gate for a **single** commit message. It turns the rule *"a correct prefix
followed by 'update X' is still an empty message — say WHY"* into a machine check instead of
a self-reported judgement. It calls no model: the same message always yields the same
verdict. The backing engine is `${CLAUDE_PLUGIN_ROOT:-.}/scripts/commit-convention-check.sh`.

## Boundary

- **This skill / script only CHECK (read-only).** They never edit git, hooks, or config.
- **Wiring it as a `commit-msg` hook is a separate, manual step** (see below). The gate emits
  a verdict; a human decides whether to install it.

## The three axes (all must hold to pass; any failure → exit 1)

- **prefix** — a Conventional Commits type: `feat / fix / docs / chore / refactor / test /
  perf / build / ci / style / revert`. Teams **extend** (never fork) the set with the
  `CCC_EXTRA_PREFIX` env var — comma- or whitespace-separated, e.g. `CCC_EXTRA_PREFIX="ops,wip"`.
- **not_empty** — the description is ≥ 6 chars and is not a bare placeholder (`update`, `wip`,
  `fix`, … alone).
- **has_why** — the description is ≥ 25 chars, OR it contains a "why" marker
  (`because` / `so that` / `in order to` / `prevent` / `avoid` / `→`, …).

`merge` / `revert` / `fixup!` / `squash!` messages are auto-generated and skipped (they pass).

## Run

```bash
# check a message from an argument (exit 1 on violation)
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/commit-convention-check.sh" \
  --msg "fix(api): retry on timeout to avoid dropped connections"

# positional form
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/commit-convention-check.sh" "feat: ... (with a WHY)"

# from a commit-message file (# comment lines are ignored)
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/commit-convention-check.sh" --file .git/COMMIT_EDITMSG

# from stdin
git log -1 --format=%B | bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/commit-convention-check.sh" --stdin
```

- exit 0 = convention met (or skipped: merge/revert/fixup/squash)
- exit 1 = violation (offending axes listed as findings)
- exit 2 = bad arguments

### Extending the allowed prefixes

Set `CCC_EXTRA_PREFIX` to add project-specific types on top of the standard set. An example
lives at `examples/commit-convention-gate/extra-prefixes.env`:

```bash
# shellcheck disable=SC1091
source examples/commit-convention-gate/extra-prefixes.env   # exports CCC_EXTRA_PREFIX
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/commit-convention-check.sh" --msg "ops: prune stale branches nightly so that history stays lean"
```

## Manual wiring (commit-msg hook — optional)

Commit-message rules belong in a `commit-msg` hook (the message does not exist yet at
`pre-commit` time). Following the `examples/git-hooks/` wiring pattern:

```sh
# 1) point git at a hooks directory you own
git config core.hooksPath .githooks

# 2) create .githooks/commit-msg that delegates to this gate:
#      #!/usr/bin/env sh
#      exec bash "$(git rev-parse --show-toplevel)/scripts/commit-convention-check.sh" --file "$1"
#    chmod +x .githooks/commit-msg
```

After wiring, do ONE live check (make a commit with an empty message and confirm it is
blocked, and a proper message and confirm it passes) before recording the hook as installed —
"configured" is not "in effect".

## Deterministic gate (self-test)

```bash
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/commit-convention-check.sh" --self-test
```

- **exit 0** — `check_msg` (prefix / not_empty / has_why) matches every built-in fixture
  (compliant → PASS, empty/why-missing → FAIL), including ASCII and multibyte cases.
- **exit 1** — some case disagrees (logic broken).
- The self-test is **hermetic**: built-in fixtures only, no git or external I/O, and it never
  hard-codes success. Decide "done" by this gate's exit code, not by self-assessment.

## Design notes

- The check runs in **python3 (standard library only)** so length is counted in Unicode code
  points — correct for multibyte languages, not just ASCII. No `jq` dependency; BSD/macOS grep
  safe (no `grep -P`).
- The message is passed to python via the `CCC_MSG` environment variable, because `python3 -`
  reads its program from stdin (so stdin is already occupied by the program heredoc).
- Sibling primitive: `commit-msg-quality-score` scores a *range* of commits by average
  quality (measurement); this skill is a **boolean gate on one message** (block/pass) for
  pre-commit use — a yes/no decision, not a threshold.

## Related

- engine: `scripts/commit-convention-check.sh` (`check_msg` / `run_selftest`)
- hook wiring pattern: `examples/git-hooks/` (README notes commit-msg vs pre-commit placement)
- example config: `examples/commit-convention-gate/extra-prefixes.env`
- primitive: `scripts/goal-loop.sh` (deterministic stop discipline)
