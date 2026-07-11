# Subagent guidelines — when to delegate vs. act inline

The kit ships four agents (`dev-explorer` / `dev-planner` / `dev-tester` / `dev-reviewer`)
and the `/build` flow orchestrates them. This doc is the missing companion: *when is
spawning a subagent worth it at all?* A subagent buys you a clean, isolated context — at
the cost of an extra round-trip, extra tokens, and a summary that can lose detail
("telephone game"). Delegate when the isolation pays for itself; otherwise just do it.

## Delegate (✅)

- **Open-ended exploration** — sweeping many files (say 10+), or **3+ independent search
  axes** (e.g. backend API + DB schema + frontend). A single `Grep`/`Glob` does not need a
  subagent.
- **Independent parallel work** — tasks with no dependency between them that are safe to run
  at once (read-only exploration especially).
- **Context protection** — research that would dump a large volume of tool output into the
  main thread and pollute its reasoning.
- **Specialized roles** — an Explore/Plan-shaped task, or a read-only review that must run
  in a *different* context from the implementer to avoid familiarity bias
  (Generator-Verifier separation).

## Don't delegate (❌)

- **Single-file work** — read/edit/write it directly.
- **One or two searches** — call `Grep`/`Glob`/`Read` directly.
- **Small, isolated changes** you can just make.
- **Redundant research** — you already have enough to act. Delegating again is waste.

## The one question

> *Can I do this myself right now?* — If **no**, delegate. If **yes**, do it.

Parallelize only genuinely-independent tasks. If results depend on each other, run them
sequentially — a barrier that waits on all of them just to feed the next stage is wasted
wall-clock.

## Type selection

| Task | Agent type |
|---|---|
| Codebase exploration / file search | `Explore` |
| Implementation strategy / architecture | `Plan` |
| Anything else compound | `general-purpose` |

## Hand-off hygiene

A subagent **cannot see this conversation**. Put everything it needs in the prompt: file
paths, the reason for the change, constraints, and the exact shape of the answer you want
back. A vague delegation returns a vague result — and you pay for the round-trip either way.

Keep the roster small. The kit deliberately ships **four** agents: flooding the set with
near-duplicate roles degrades automatic delegation because the model can no longer tell them
apart. Reuse the four before inventing a fifth.
