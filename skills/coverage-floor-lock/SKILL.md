---
name: coverage-floor-lock
description: Read-only guard that checks test coverage against a floor (lower-bound threshold) AND detects floor-lowering diffs — the anti-gaming case where the floor is quietly reduced so a dropping coverage number still passes. Exits 1 on below-floor or a lowered floor. Triggers: coverage floor, coverage lower bound, below floor, floor lowered, coverage gate, coverage guard.
---

# coverage-floor-lock

Reads a coverage report (lcov / json / plain-text %) and:

1. checks that coverage % is **at or above the floor** (below-floor detection), and
2. checks that the **floor itself was not lowered** vs a baseline — the anti-gaming
   case where someone quietly drops the floor so a falling coverage number still
   "passes" (a **floor-lowering diff**).

It performs **inspection (read) + a printed verdict** only. It never changes the floor
value, CI config, or branch protection.

## Boundary (read-only)

- This skill and `${CLAUDE_PLUGIN_ROOT:-.}/scripts/coverage-floor-check.sh` only inspect and
  print a verdict. They never write the floor value, wire it into CI, or touch branch protection.
- Raising the floor / improving coverage is done **by hand**. Wiring a coverage floor into a
  **CI required check or branch protection is a high-risk (self-lockout) change** — apply it
  deliberately with human review (a `/review` session, the kit's read-only `dev-reviewer`
  agent, or a person).
- **It never calls git.** The baseline (previous floor) is supplied by the caller via
  `--baseline-floor` / `--baseline-floor-file`. In CI, the caller resolves it (e.g.
  `git show HEAD~1:.coverage-floor`) and passes it in.

## How to run

1. Generate a coverage report with your project's test runner (this skill does not generate it).
2. Inspect (read-only):
   ```
   bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/coverage-floor-check.sh" \
       --coverage <file> --floor <N> [--baseline-floor <M>]
   ```
   - `--coverage`: lcov (`.info`) / json (istanbul summary etc.) / text (`NN.N%` or a ratio
     `0.NN`). `--format auto|lcov|json|text` (default `auto`).
   - `--floor <N>`: current lower bound (%). Or `--floor-file <path>` (extracts a bare number /
     `{"coverage_floor":N}` / `floor: N`).
   - `--baseline-floor <M>` (optional): the previous lower bound. When set, **current < previous
     → floor lowered** = fail. `--baseline-floor-file <path>` also works.
   - **exit 0** = coverage >= floor AND floor not lowered (pass).
   - **exit 1** = below floor or floor lowered (fail).
   - **exit 2** = missing/invalid arguments.
   - **exit 3** = coverage / floor unparseable → **state UNKNOWN** (an empty/missing report is
     not read as 0%).
3. On exit 1, raise the floor or improve coverage **by hand** (CI-gate changes need human
   approval). On exit 3 (UNKNOWN), fix the report generation first — do not treat it as 0%.

## Deterministic --check (self-test)

```
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/coverage-floor-check.sh" --self-test
```

- **exit 0** = the verdict logic (at-or-above-floor → pass / below floor → fail / floor lowered
  → fail / unparseable → UNKNOWN) and the lcov/json/text parsers agree across all built-in
  fixtures.
- **exit 1** = a case disagreed (broken logic).
- The self-test is gh/aws/**git-independent** and hermetic (temp `mktemp -d` fixtures only; it
  writes nothing into the repo and cleans up). No hardcoded success — changing one expected
  value makes it FAIL.

## Example config

- `examples/coverage-floor/coverage-floor.json` — a minimal floor file (`{"coverage_floor": 80}`)
  you can pass via `--floor-file` and version alongside the code so the baseline can be resolved
  from git history in CI.

## Related

- backing script: `${CLAUDE_PLUGIN_ROOT:-.}/scripts/coverage-floor-check.sh`
- stop primitive: `scripts/goal-loop.sh` (exit 0 = GOAL MET / 1 = CONTINUE / 2 = CAP REACHED)
