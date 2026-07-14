---
name: judge-rubric-vote
description: Aggregate N independent judge votes (verdict + optional score) into a majority verdict gated by a deterministic agreement threshold. A split panel is "hung" and FAILS instead of being spun as a pass; no votes = observe (exit 2), never a fail-open pass. No LLM call — pure deterministic tally. Triggers: judge vote, judge-vote, rubric vote, majority verdict, LLM judge panel, agreement threshold, hung jury.
allowed-tools: Bash, Read, Write, Glob, Grep
---

# judge-rubric-vote

Never trust a single judge's self-report. This gate takes **N independent judge
votes** and aggregates them into one majority verdict — but the verdict only
stands when the **agreement rate clears a deterministic threshold**. When the
votes split, the panel is **hung**, and hung **FAILS**; it is never rounded up
into a pass. The tally is deterministic (a `Counter` over the verdicts, no LLM
call), so the same votes always produce the same verdict.

```
agreement = (votes for the majority verdict) / (valid votes)
pass  iff  agreement >= --agree (default 0.66)
otherwise -> hung -> fail
```

## Input format

`--votes` takes a JSON **list**; each element is an object with a required
`verdict` (any JSON value) and an optional numeric `score`. `-` reads the list
from stdin.

```json
[
  {"verdict": "pass", "score": 0.9},
  {"verdict": "pass", "score": 0.85},
  {"verdict": "fail", "score": 0.4}
]
```

- Elements without a `verdict` key are **ignored** (not counted as votes).
- `score` is optional; it feeds `mean_score` / `score_variance` only, never the
  verdict.

## Status / exit codes

| status | when | exit | meaning |
|---|---|---|---|
| `pass` | `agreement >= --agree` | **0** | consensus — adopt the majority verdict |
| `fail` | `agreement < --agree` | **1** | `hung` — the panel is split; re-judge or add votes |
| `observe` | missing file / non-JSON / not-a-list / 0 valid votes | **2** | not measurable — do not adopt |

`observe` exits **2, not 0, deliberately.** A standalone gate that exited 0 when
the votes file was missing would be **fail-open**: a `&&` caller would read "no
votes" as consensus. `2` = "unknown, do not adopt" (the kit's abstain/unknown
convention), so absence can never be mistaken for agreement.

## How to run

```
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/judge-vote.py" --votes <votes.json> [--agree 0.66] [--json]
```

- Default output is a human-readable summary; `--json` emits a dict with
  `subcommand` (`"judge-vote"`), `status`, `score` (= agreement), `threshold`,
  `metrics` (`votes` / `verdicts` / `majority` / `agreement` / `mean_score` /
  `score_variance`), `findings`, and `verdict` (the adopted majority verdict).
- Branch on the exit code: `0` adopt, `1` the panel is hung (escalate / re-judge),
  `2` you have no usable votes.

### score_variance — the "agree on the verdict, disagree on the score" signal

`score_variance` is the population variance of the votes' scores. It is a
**visibility** number, not part of the pass/fail decision: judges can all vote
`pass` (agreement 1.0) while their scores are `0.5` / `0.9` / `0.6`. High variance
next to a passing verdict means the panel agrees on the label but not on the
degree — worth a human glance even though the gate passed.

## Deterministic health check (`--self-test`)

The script's own correctness is machine-verified, not self-reported:

```
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/judge-vote.py" --self-test
```

- **exit 0 = PASS.** It builds hermetic tempfile fixtures and runs them through
  the real load path (no hardcoded success): 3/3 unanimous → pass (exit 0);
  3-way split (agreement 0.33) → hung/fail (exit 1); missing file → observe
  (exit 2); non-list JSON → observe; a vote lacking `verdict` ignored (2 valid +
  1 invalid → pass, `votes` = 2); and a 2-of-3 case that **straddles** the bar
  (agreement 0.667 passes at `--agree 0.66` but fails at `--agree 0.7`) to prove
  the threshold is live.
- **non-zero**: the tally logic, the threshold, or an exit-code mapping has
  regressed. CI / periodic health checks treat the exit 0 as the green floor.

## When to use / not

- ✅ You have (or can produce) several independent judge verdicts on one artifact
  and want a deterministic, fail-closed consensus decision.
- ❌ Only one judge / no independent second opinion (there is nothing to
  aggregate), or a decision that is not a discrete verdict.

## Sibling gates

- `adversarial-refute-vote` — the adversarial counterpart: instead of judges
  voting a verdict, N reviewers try to **refute** one claim; a majority of
  successful refutations stops adoption.
- `fanout-verify-synth` — a stop-judge over a fan-out verification result
  (`verify.json`): decides whether every claim is verified before synthesis.
- `scripts/goal-loop.sh` — the generic stop primitive (exit 0 = GOAL MET / 1 =
  CONTINUE / 2 = CAP REACHED); wrap this vote as a loop's `--check` when a step
  should only advance on a clean panel.
