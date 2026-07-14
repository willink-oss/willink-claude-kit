---
name: live-state-verify-guard
description: Scan a report body and flag every state-claim (merged/deployed/published/released/done) that has no live probe (gh/curl/aws/git) preceding it in the same section. A read-only, deterministic audit — one unverified claim exits 1; a document is plan, live is state. Triggers: live-state audit, unverified claim, status-claim verification, report verify guard, no self-report, doc-vs-live.
allowed-tools: Bash, Read, Glob, Grep
---

# live-state-verify-guard

Scan a report body (a status update, a standup, a daily report) and, for every
**state-claim** — *merged / deployed / published / released / shipped / done / in
production / up and running* — check that a **live probe** (`gh` / `curl` / `aws` /
a cloud CLI / `git` / GitHub MCP) appears **BEFORE** that claim **in the same
section**. A claim with no preceding probe in its section is an *unverified claim*
and is flagged; one or more of them makes the audit exit 1. The principle is **no
self-report**: a document is *plan*; live is *state*.

This is the **post-hoc audit** counterpart to the read-only [`/pulse`](../commands/pulse.md)
command (which renders a dashboard only from what a deterministic probe returned)
and to the pre-report advisory hook `examples/hooks/pre-status-verify-guard.sh`
(which nudges *before* you write). `/pulse` measures, the advisory hook nudges, and
this skill machine-checks the body *after* you wrote it.

## Detection heuristic

The engine walks the body one line at a time, tracking a `live_seen` flag.

- **`LIVE_RE`** — live-probe markers are **command signatures**, not prose, so that
  "the git repository" does not false-trigger: `gh pr|run|api|workflow|release…`,
  `git ls-files|log|rev-parse|show…`, `aws <service>`, a cloud CLI
  (`kubectl|docker|terraform|gcloud|az|psql|redis-cli`), `curl`, `mcp__(plugin_)github`.
- **`CLAIM_RE`** — state-claim phrases: `merged|deployed|published|released|shipped`,
  `rolled out|in production|went live|is live|up and running`, `completed|done|passing`.
- A claim is **verified** if a live marker appeared at or before its line **within
  the same section**, else **unverified**. **>= 1 unverified → exit 1.**
- **Evidence is scoped.** A **blank line**, a **markdown heading**, OR the start of a
  **new list item** (`-`, `*`, `+`, `1.`) resets `live_seen`, so an opening probe
  cannot rubber-stamp the whole document — the probe must be co-located with the claim
  in the same paragraph, section, *or bullet*. The list-item reset matters because
  status reports are bullet lists with no blank line between items; without it, one
  probe bullet would verify every following claim bullet and hollow out the gate.

The flags are **heuristic candidates**, not verdicts, and coverage is deliberately
**recall-first**: a *missed* claim (an unverified doc exiting 0) is the dangerous mode,
so `CLAIM_RE` errs toward matching (it will over-match some prose). A summary or
citation of a past measurement can false-positive; a human reads the snippet to
separate a real unverified claim from a legitimate citation. Register any domain- or
language-specific claim phrasings the built-in set misses via `LSA_EXTRA_CLAIMS`.

## How to run

```
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/live-state-audit.sh" --report <path>     # audit a file
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/live-state-audit.sh" < report.md          # audit stdin
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/live-state-audit.sh" --report <path> --json  # machine-readable
```

| exit | meaning |
|---|---|
| 0 | no unverified claims (no state-claim at all, OR every claim had a preceding live probe in its section) |
| 1 | >= 1 unverified claim (a state-claim with no preceding live-probe marker) |
| 2 | argument error (e.g. `--report` file missing) **or** a config error (a malformed `LSA_EXTRA_LIVE` / `LSA_EXTRA_CLAIMS` regex) — distinct from 1 so a wrapper never reads a broken pattern as an "unverified claim" |

Then: (1) read each `XX unverified` line — `L<line> [phrase] snippet`; (2) separate
a real unverified claim (needs fixing) from a citation of a past measurement (false
positive); (3) for a real one, run a **live probe** before the claim
(`gh pr view <N> --json state,mergedAt` / `curl -w "%{http_code}"` /
`aws … list-jobs`), fix the wording, and re-run until exit 0 before recording "done".

## Read-only boundary

This skill **detects, it does not fix**. `scripts/live-state-audit.sh` reads the
report body only — it never touches git, deploy, secrets, or any external service.
Correcting a flag (re-measuring, then editing the wording) is a **human** step, and
anything externally visible, financial, or legally binding stays a human decision
regardless of what this gate returns.

## Deterministic self-test (--check)

Do not self-report "the audit works". Machine-verify it:

```
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/live-state-audit.sh" --self-test
```

- **exit 0 = PASS.** In-memory fixtures run through the SAME `audit()` the CLI uses
  (no hardcoded success): gh/curl/cloud-CLI probe *before* a claim → 0; a claim with
  no probe → 1; no claim phrase → 0; a probe *after* the claim → 1; a claim in a
  separate blank-line paragraph → 1; a claim under a different heading → 1.
- **non-zero** = the audit logic is broken; fix the engine before trusting a flag.
- The self-test is hermetic (in-memory fixtures only; no external read/write; it
  never runs a live probe).

## Extension env vars

Extend the patterns without editing code (useful for a non-English or
domain-specific team):

- `LSA_EXTRA_LIVE` — if set, `|` + its value is appended to `LIVE_RE` (register
  extra probe command signatures, e.g. `flyctl|heroku`).
- `LSA_EXTRA_CLAIMS` — if set, `|` + its value is appended to `CLAIM_RE` (register
  extra state-claim phrases). Both are ERE alternation fragments.

## Related

- command: [`/pulse`](../commands/pulse.md) — the read-only measurement layer this
  audit complements (it renders only what a probe returned).
- pre-report advisory: `examples/hooks/pre-status-verify-guard.sh` — nudges *before*
  you write; this skill machine-checks *after*.
- profile: `docs/harness-profile.md` principle 6 — "read live state before you
  report it" (docs are *plan*; live is *state*).
