---
name: maker-checker-relay
description: Relay a Generator (implementer = Maker) and a read-only Verifier (Checker — the kit's dev-reviewer agent, a /review session, or a human) until tests are green AND the Checker has zero blocking findings. A goal-loop wrapper whose stop condition is a deterministic gate, not the model's self-report. Triggers: maker-checker, generator-verifier relay, implement-then-review loop, no self-review, /review loop, relay.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent
---

# maker-checker-relay

A wrapper over `scripts/goal-loop.sh` (the stop primitive) that separates the **Generator
(implementer = Maker)** from the **Verifier (a read-only reviewer = Checker)** and relays
between them until **BOTH** hold:

- **Maker's tests are green** (a deterministic test command exits 0), and
- **the Checker has zero blocking findings** (a `review.out` with no `BLOCKER` lines).

This is the kit's Generator-Verifier principle applied to the *fix loop*. The point is to
remove the self-review bias — an implementer who reviews their own diff and declares it
"fixed". "Done" is decided by a deterministic gate's exit code, never by self-report; the
loop is bounded by `--max` (default 3) so it always terminates.

## The Checker role

The Checker is any **read-only** reviewer that writes findings to a file — in this kit the
natural fit is the **`dev-reviewer` agent** (spawn it read-only on the diff), but a
`/review` session or a human works identically. What matters is that it runs in a *separate
context* from the Maker and marks each blocking finding with a line containing `BLOCKER`
(non-blocking nits are left unmarked).

## How to run

1. Decide the Maker DoD (a deterministic test command, exit 0 = green) and the Checker's
   findings file `review.out`.
2. Advance the relay one step at a time (call at the top of each cycle, branch on exit code):
   ```
   bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/maker-checker-relay.sh" --tick \
       --test "<Maker's deterministic test>" \
       --review-out review.out [--blocker-pattern BLOCKER] \
       [--goal "<desc>"] [--max 3] [--state .goal-loop-maker-checker.state]
   ```
   - **exit 1 (🔁 CONTINUE)**:
     - **Maker** (implementer): make ONE increment toward green tests. Do **not** self-review.
     - **Checker** (read-only reviewer — the `dev-reviewer` agent, `/review`, or a human):
       review the diff, write findings to `review.out`, and put `BLOCKER` on each blocking
       finding's line (omit nits). Then re-run `--tick`.
   - **exit 0 (✅ COMPLETE)**: tests green AND zero `BLOCKER` lines. Commit / PR.
   - **exit 2 (🛑 CAP)**: over `--max`. Stop and escalate to a human; record the unmet goal
     wherever you track blockers.
3. Reset the attempt counter with `--reset` when starting a new relay.
4. To wire it into goal-loop by hand, `--print-check` prints the `--check` string (already
   pointed at `--gate`, so the pass/fail logic is defined once).

## Deterministic --check (self-test)

```
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/maker-checker-relay.sh" --self-test
```

- **exit 0**: the wrapper is healthy — verified `bash -n`, that it references `goal-loop.sh`,
  the stop wiring (`--reset/--check/--max/--state`), the pass/fail truth table (7 cases:
  green+clean = pass; green+blockers, red+clean, red+blockers, green+not-reviewed,
  no-test-command, and a malformed `--blocker-pattern` = fail — the gate fails CLOSED when
  its own matcher errors), and `--print-check` end-to-end (2 cases), all on hermetic
  fixtures (`mktemp -d`, cleaned up — no repo residue).
- **non-zero**: a gap in the pass/fail logic, wiring, or syntax.

## When to use / not

- ✅ A deterministic Maker DoD exists (green tests) and independent review matters.
- ❌ No deterministic test, or a single-shot change that needs no iteration (use `/build`).

## Related

- primitive: `scripts/goal-loop.sh` (exit 0=GOAL MET / 1=CONTINUE / 2=CAP REACHED)
- wrapper: `scripts/maker-checker-relay.sh` (`--gate` / `--print-check` / `--tick` / `--reset` / `--self-test`)
- Verifier role: the `dev-reviewer` agent (read-only), or a `/review` session
- command: `/goal-loop` (`commands/goal-loop.md`); profile: `docs/harness-profile.md`
