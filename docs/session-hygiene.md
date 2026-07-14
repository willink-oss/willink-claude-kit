# Session hygiene — when to start a new session, compact, or rewind

A long-lived Claude Code session accumulates context that dilutes the model's attention and
raises cost. These are the defaults the kit's agents follow; adopt them near-verbatim.

## New task = new session

Start a **fresh session** when the *task* changes, even if the repo doesn't:

- switching from `/build`ing one component to an unrelated one
- moving between phases — research → design → implement → review
- an independent review or audit
- a status check (`/pulse`) that isn't part of the change you're mid-way through

Carrying a finished task's context into the next one makes the model reason over stale,
irrelevant detail.

## Same session is fine

Keep going in the **same session** for one continuous task:

- implement → test → commit of a single change
- error → investigate → fix
- consecutive edits to the same file(s)

## Compact with a hint

When context is heavy but the task isn't done, `/compact` with an explicit instruction so
the summary keeps what matters:

```
/compact keep only the current PR context (files touched, failing test, next step)
```

Rough triggers: **> ~30 tool calls**, **> ~60% context**, or **before a task-context switch**.
Prefer proposing `/compact` over silently continuing a bloated session.

## Wrong turn? Rewind, don't argue

If the session went the wrong direction, **Esc+Esc to rewind** beats a "no, actually…"
correction prompt — the latter leaves the wrong context in history where it keeps
influencing the model. After a bad commit, fix it with `git commit --amend` (or `git revert`),
not a new "fix the last thing" turn.

## Why this matters

Context is the model's working memory. Every stale task, abandoned approach, and dumped
tool result competes with the current task for attention. Session hygiene is context
hygiene: it is the cheapest reliability lever you have, and it costs nothing but discipline.
