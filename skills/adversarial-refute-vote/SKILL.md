---
name: adversarial-refute-vote
description: Put one claim to N independent refute votes and stop adoption when a strict majority vote "refuted". A deterministic aggregation gate — the threshold decision comes from a tally, never from the model's self-report; no LLM is called. Triggers: adversarial refute, refute vote, refute-vote, majority refute, claim verification vote, refute gate.
allowed-tools: Bash, Read, Write, Glob, Grep
---

# adversarial-refute-vote

Don't adopt a claim because it "looks right". Put it to **N independent refute votes**
and let a deterministic tally decide: if a **strict majority** of the votes refuted the
claim, adoption **stops**. The threshold decision is made by counting — never by the model
reporting "I'm confident this is fine". The engine (`scripts/refute-vote.py`) is python3
standard library only, calls no LLM and no external API, and has no side effects beyond an
exit code and its printed report.

## Purpose

- Aggregate N refute votes (each `{"refuted": bool}`) and machine-decide **majority
  refuted → stop adoption / not a majority → may adopt**.
- Each judge votes independently on one question — "can this claim be refuted?
  (refuted=true/false)" — and only those verdicts are tallied. Scoring and synthesis may be
  done by a model upstream, but **the adopt/stop threshold is decided by this gate**, not by
  self-report.
- A state where no votes were obtained (zero valid votes) is **not** collapsed into "no
  refutation = adopt". Empty output != zero — it is reported as `abstain`.

## Input format

Pass a JSON array file to `--votes` (`-` for stdin). Each element is a dict with a required
`refuted` (bool) and an optional `reason` (string).

```json
[
  {"refuted": true,  "reason": "source does not support the claim"},
  {"refuted": true,  "reason": "figure contradicts the measured value"},
  {"refuted": false}
]
```

- An element whose `refuted` is not a bool (missing, a string, a number) is an **invalid
  vote** and is excluded from the tally.
- If there is not a single valid vote, the decision is `abstain` (undecidable, exit 2). It
  is never tipped toward adopt.

## Decision table (strict majority)

| Condition | decision | exit | Meaning |
|---|---|---|---|
| `refuted * 2 > valid` | `stop` | 1 | Majority refuted → **stop adoption** |
| `refuted * 2 <= valid` | `adopt` | 0 | Not a majority → may adopt |
| `valid == 0` / bad input | `abstain` | 2 | Undecidable (treated as unknown) |

- With N=3, two or more refute votes stops adoption.
- An **even split is NOT a majority** (e.g. 2 refutations of 4 votes) → `adopt`.
- `abstain` on zero valid votes is deliberate: **empty output != zero**. No votes obtained
  means "take the votes again", not "nobody refuted, so adopt".

## How to run

1. Have each refute judge write its verdict to a JSON array file
   (`{"refuted": ..., "reason": ...}`).
2. Run the aggregation:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/refute-vote.py" --votes <votes.json> [--json]
   ```
3. Branch on the exit code (or the `decision` field when `--json` is used):
   - `0` (adopt) … not a majority → may adopt.
   - `1` (stop) … majority refuted → stop adoption (re-verify or discard the claim).
   - `2` (abstain) … no valid votes / bad input → take the votes again (do not proceed).
4. `--json` output carries `decision` / `total` / `valid` / `invalid` / `refuted` /
   `upheld` / `majority` / `reason`.

## Deterministic self-check (--check)

The engine's own health is not self-reported — it is machine-verified by a hermetic
self-test:

```
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/refute-vote.py" --self-test
```

- **exit 0 = PASS.** It covers 8 cases: majority refuted (2/3, unanimous) → stop; minority
  (1/3, 0/3) → adopt; even split (2/4) → adopt; zero valid votes, empty array, and
  wrong-type entries → abstain.
- Each fixture is written to a tempfile and run through the same `load → aggregate` path as
  production — there is no hardcoded success.
- **non-zero**: a gap in the decision truth table.

## Safety note

The script is **read-only aggregation** — no external API, no LLM, no mutation; the only
outputs are an exit code and a printed report. The `adopt` signal is a **gate, not an
approval**: it says the claim survived majority refutation, nothing more. Anything
externally visible, financial, or legally binding still requires explicit human approval
regardless of what this gate returns.

## Sibling gates

- `judge-rubric-vote` — a majority-verdict gate over judge votes that also enforces an
  **agreement threshold** (hung/undecided when the votes disagree too much), where this gate
  is a single strict-majority refute test.
- `fanout-verify-synth` — the **fan-out claim verification synthesis gate**: it reads the
  results of multi-agent verification and decides whether every claim is verified before
  synthesis is allowed.
- `scripts/goal-loop.sh` — the same stop-primitive family: a deterministic gate's exit code
  (not the model's judgement) decides "done / continue / cap".
