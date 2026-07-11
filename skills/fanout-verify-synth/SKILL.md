---
name: fanout-verify-synth
description: Read the collected result of a fanned-out, adversarially verified claim set (verify.json) and decide DETERMINISTICALLY whether every claim is really verified, so synthesis is gated on data instead of eyeballing. A stop primitive in the goal-loop family whose verdict is an exit code, not the model's self-report. Triggers: fan-out verification, verify.json gate, synthesis gate, claim verification stop, adversarial verify, multi-agent verification.
allowed-tools: Bash, Read, Write, Glob, Grep
---

# fanout-verify-synth

A deterministic **go/no-go gate for synthesis** after a fanned-out verification. In a
deep-research / multi-agent fan-out, several agents each emit claims, and each claim is
verified adversarially. Before you synthesize the final artifact you must answer one
question — *is every claim actually verified?* — and the reliable way to answer it is a
machine gate, not eyeballing a list. This script reads the collected `verify.json` and
returns the answer as an exit code.

It is **not** the part that runs the fan-out (spawning agents, executing verification). Like
`scripts/goal-loop.sh`, it is the stop / verify primitive: it only decides whether to STOP
and synthesize. "Done" is decided by the gate's exit code, never by the model reporting
"everything checked out".

The decision forbids self-report: a bare `verified` label is not enough. A claim is counted
as effectively verified only if it also carries at least `--min-sources` pieces of evidence —
**a label is self-report; the evidence count is data.**

## When to use / not

- ✅ A fan-out produced claims, each adversarially verified, and you need a deterministic
  go/no-go before synthesizing the report.
- ✅ You want "is everything backed up?" settled by a gate, not by reading down a list.
- ❌ There is no per-claim status/evidence to gate on, or a single unverified assertion you
  can just check by hand.

## verify.json schema

```json
{
  "topic": "the research question",
  "claims": [
    { "id": "c1", "claim": "one-line summary", "status": "verified",
      "sources": ["https://...", "https://..."], "agent": "agent-a" },
    { "id": "c2", "claim": "...", "status": "refuted", "sources": ["..."] }
  ]
}
```

- `status`: one of `verified` | `refuted` | `unverified` | `pending`.
- `sources`: evidence URLs for the verification. A `verified` claim with fewer than
  `--min-sources` (default 1) sources is **demoted to held** — a label without evidence is
  treated as self-report, not as verified.

## How to run

Aggregate each verification agent's result into a single `verify.json` (record `status` and
`sources` for every claim), then judge:

```
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/fanout-verify-synth.sh" --in verify.json [--min-sources 1] [--json]
```

Branch on the exit code:

| exit | verdict | meaning | what the caller does |
|---|---|---|---|
| `0` | ✅ **ADOPT** | every claim is effectively verified | synthesize the final artifact |
| `1` | 🔁 **HOLD** | unverified / pending / under-sourced claims present, no refutations (zero claims is also HOLD, fail-safe) | re-verify the held claims or fan out more, then re-judge |
| `2` | 🛑 **CONFLICT** | at least one refuted claim | do not synthesize — escalate to a human |
| `3` | — | usage / JSON parse error (missing file, broken JSON, `claims` not an array) | fail-safe to non-adoption; fix the input |

Use `--json` to get a machine-readable verdict (`verdict`, `exit`, counts, plus `held_ids`
and `refuted_ids`) so you can open a re-verification task for exactly the claims that failed.

### The evidence rule

The `verified` label alone never adopts a claim. `--min-sources N` (default 1) sets how many
evidence URLs a `verified` claim must carry; below that it is demoted to held and the whole
set falls to HOLD. Setting `--min-sources 0` relaxes the rule (a bare label counts) — useful
only when evidence lives elsewhere. This is the same "a label is self-report, evidence is
data" principle the sibling gates enforce.

## Deterministic --check (self-test)

```
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/fanout-verify-synth.sh" --self-test
```

- **exit 0 = PASS.** It exercises the real `decide()` path on hermetic `mktemp` fixtures (no
  hardcoded success) across 9 cases: all-verified→ADOPT, unverified-mixed→HOLD,
  refuted→CONFLICT, verified-but-under-sourced→HOLD, `--min-sources 0` relaxes the same
  input→ADOPT, empty-claims→HOLD (fail-safe), broken-JSON→error, claims-not-array→error, and
  missing-file→error.
- **non-zero**: a gap in the verdict truth table or syntax.

## Sibling gates

Same stop-primitive family — all deterministic, all "a label is self-report, evidence is
data", all delegating the terminate decision to an exit code:

- `adversarial-refute-vote` — puts a single claim to N independent refute votes and stops
  adoption when a majority refutes it.
- `judge-rubric-vote` — disciplines multiple judges' verdicts by majority + vote spread and
  calls a hung decision when agreement is below threshold.
- `scripts/goal-loop.sh` — the underlying stop primitive (exit 0=GOAL MET / 1=CONTINUE /
  2=CAP REACHED) that this gate's exit-code contract mirrors.
