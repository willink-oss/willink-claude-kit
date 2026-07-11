---
name: self-heal-ci
description: Detect a red CI and relay "fix → re-verify" until CI is green, BOUNDED by an attempt cap. A goal-loop wrapper whose stop condition is a deterministic gate (gh conclusion=success), not the model's self-report. Triggers: self-heal-ci, self heal ci, CI self-heal, red CI heal, fix failing CI, keep fixing CI until green.
allowed-tools: Bash, Read, Edit, Glob, Grep
---

# self-heal-ci

A wrapper over `scripts/goal-loop.sh` (the stop primitive) that **self-heals a red CI**:
when the latest run is red it relays "fix → re-verify" until CI is green, or until an
attempt cap is reached. The stop / counting discipline is delegated to `goal-loop.sh` — this
wrapper only drives detection and the fix cycle, it never loops forever on its own.

The goal test is a **single deterministic point**: `gh run list -L1` → latest run's
`conclusion == "success"`. Everything else (`failure` / `cancelled` / `timed_out` /
`null`=in-progress / `""`=fetch failed) is treated as **red** (fail-safe — an empty gh
result is "red (unknown)", never "0 runs / green"). "Done" is decided by that gate's exit
code, never by the model reporting "I fixed it".

## How to run

1. From the target repository's working tree (or with `--repo O/R`), start the loop:
   ```
   bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/self-heal-ci.sh" [--repo <owner/repo>] [--max <N>] [--escalate-file <path>]
   ```
2. Each round the wrapper calls `goal-loop.sh` and measures whether the latest run is green:
   - **green (exit 0)** → ✅ done, stop.
   - **red & under cap (exit 1)** → run ONE fix cycle. If `claude` is on PATH it runs
     headless (one fix = feature branch + PR, following `CYCLE-PROMPT.md`). With `DRY_RUN=1`
     or no `claude` on PATH it is gate-only: it returns `exit 1` so an outer caller fixes and
     re-runs.
   - **cap reached (exit 2)** → escalate (see below) and stop, so it never burns tokens forever.
3. Fixes are **PR-only**. No self-merge / tag push / push to main — that boundary lives in
   `CYCLE-PROMPT.md` (the prompt the fix agent reads).
4. The Verifier of "is it really green?" is a deterministic gate, not the fixer. Independent
   review of the actual diff can be done by the kit's `dev-reviewer` agent (read-only), a
   `/review` session, or a human.

### Modes / options

| Flag / env | Meaning |
|---|---|
| (none) | run the self-heal loop (default) |
| `--repo O/R` | target repository (defaults to the cwd repo) |
| `--max N` | attempt cap (default 5; `SELF_HEAL_MAX` env also works) |
| `--escalate-file <path>` | on CAP, append the escalation line here instead of stdout (`SELF_HEAL_ESCALATE_FILE` env also works) |
| `--state <path>` | where the attempt counter persists (`SELF_HEAL_STATE` env; default `.self-heal-ci.state`) |
| `--ci-check` | exit 0 if latest run is green, exit 1 if red (the body of goal-loop's `--check`) |
| `--self-test` | gh-independent deterministic self-test (exit 0 = PASS) |
| `DRY_RUN=1` | gate-only: detect once and exit; do not launch `claude` (smoke test) |

### Escalation sink

On CAP the wrapper prints a single escalation line to **stdout** by default. Pass
`--escalate-file <path>` (or set `SELF_HEAL_ESCALATE_FILE`) to append it to a file of your
choice instead — e.g. an action list, a log, or an issue body draft. No path is hardcoded.

## Deterministic --check (self-test)

The skill / script's own health is machine-verified without any dependency on `gh`:

```
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/self-heal-ci.sh" --self-test
```

- **exit 0 = PASS.** It verifies (no hardcoded success):
  1. `bash -n` (its own syntax).
  2. that it references `goal-loop.sh` and that the stop primitive exists.
  3. the `ci_conclusion_ok` predicate on fixtures (only `success` is green; every other
     value, including empty and `SUCCESS`, is red).
  4. the `--ci-check` path end-to-end with a **fake gh** (hermetic fixture):
     `success`→exit0 / `failure`→exit1 / empty→exit1 (fail-safe).
- **non-zero**: a gap in the conclusion truth table, the wiring, or syntax.

## When to use / not

- ✅ A repo has a red CI you want driven to green with a bounded, deterministic stop.
- ❌ No `gh`-reported CI, or a one-off manual fix that needs no re-verify loop (use `/build`).

## Related

- primitive: `scripts/goal-loop.sh` (exit 0=GOAL MET / 1=CONTINUE / 2=CAP REACHED)
- wrapper: `scripts/self-heal-ci.sh` (`--ci-check` / `--self-test` / `--repo` / `--max` / `--escalate-file`)
- fix cycle prompt: `skills/self-heal-ci/CYCLE-PROMPT.md`
- Verifier role: the `dev-reviewer` agent (read-only), a `/review` session, or a human
- command: `/goal-loop`; the green-CI pattern lives in `examples/ci/all-checks-pass-pattern.md`
