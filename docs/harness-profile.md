# Harness profile — deterministic gates for AI-driven repos

A minimal, proven set of *deterministic* enforcement layers for repositories where an AI
coding agent does most of the work. Telling the agent "follow our standards" is
probabilistic; a gate that exits non-zero is not. This profile packages what the kit's
home organization enforces in production (ADR-019).

## The ladder

| Layer | What | Where in this kit |
|---|---|---|
| H1 | Docs / natural-language rules | `CLAUDE.md`, `examples/project-standards-template/`, [`a11y-standards`](../skills/a11y-standards/) |
| H2 | AI semantic review | PR review agents (advisory) |
| H3 | **Blocking verification** | hooks ([`docs/hooks-guide.md`](hooks-guide.md), [`examples/hooks/`](../examples/hooks/)) + CI required checks ([`examples/ci/`](../examples/ci/)) + deterministic gates (`skills/` coverage-floor-lock · token-codegen-gate · commit-convention-gate · self-heal-ci · live-state-verify-guard · a11y-static-gate) |
| H4 | Structural tests | architecture / parity tests — [`architecture-parity-gate`](../skills/architecture-parity-gate/) (config-declared dependency direction + layer naming) |

Rules start at H1 and get **promoted** when violated repeatedly (2+ of the same kind).
Demoting or loosening a gate is a governance decision — require explicit human approval.

## Principles (deterministic-first)

1. When adding a rule, first ask: *can a linter / test / hook / CI job fail on this?*
   Natural language is the fallback, not the default.
2. Gates block by default (`exit != 0`). Warnings are ignored by agents — if it matters,
   fail.
3. Set thresholds the agent can comfortably meet even when humans can't (e.g. high branch
   coverage). Never lower them; never exclude files to pass.
4. After every incident, add **one** verification that would have caught it.
5. Documents count too: articles, reports and changelogs can be linted (tone, citations,
   measured-value markers) like code.
6. **The user-facing surface counts too.** "Be accessible" is the most probabilistic instruction
   in a codebase, and its failures are invisible to the person writing the code — the control
   still looks like a button. So the same ladder applies: the contract is H1
   ([`a11y-standards`](../skills/a11y-standards/)), the blocking check is H3
   ([`a11y-static-gate`](../skills/a11y-static-gate/), which fails on an interactive element with
   no accessible name / role / state), and the runtime tests live with the stack
   ([`examples/a11y/`](../examples/a11y/)). Adopting it on a repo that already has violations uses
   a **baseline + ratchet**: the known set is frozen, only new violations fail, and the baseline may
   only shrink — the same anti-gaming shape as `coverage-floor-lock`, where lowering the floor is
   itself the finding. Note what the gate does *not* claim: green means "no statically detectable
   defect", never "accessible" — a screen-reader pass stays a human step.
6. Read live state before you report it. Status / progress / "is it deployed?" claims must
   come from a live probe, not a document (docs are *plan*; live is *state*). The read-only
   [`/pulse`](../commands/pulse.md) command is the measurement layer for this profile — it
   renders only what a deterministic probe returned and writes `❓` when a probe fails, so a
   green dashboard cannot be hallucinated. Two enforcers pair with it: the fail-open advisory
   hook [`examples/hooks/pre-status-verify-guard.sh`](../examples/hooks/pre-status-verify-guard.sh)
   injects a "measure before you report" reminder when a prompt asks for status, and the
   read-only audit gate [`live-state-verify-guard`](../skills/live-state-verify-guard/)
   (`scripts/live-state-audit.sh`) scans a finished report and exits non-zero if any
   status-claim (merged / deployed / released / done) is not backed by a live probe *earlier
   in the same section* — a blank line or heading resets the evidence scope, so one opening
   probe cannot rubber-stamp the whole document.

## Autonomous loops

An agent that loops until "done" must not decide "done" itself. Bound every autonomous loop
with a **deterministic stop check** and a **hard attempt cap**. The
[`/goal-loop`](../commands/goal-loop.md) command wraps `scripts/goal-loop.sh` for exactly
this: it stops only when a `--check` command exits 0 (green tests, coverage ≥ T, lint = 0)
and caps retries so the loop always terminates — no self-reported "I think it's done".
`maker-checker-relay` (`skills/maker-checker-relay/`) extends it to a Generator↔Verifier
loop — implementer vs. read-only reviewer — that completes only when tests are green AND the
reviewer has zero blocking findings.

When a loop's stopping decision is judged by AI — a review, a research claim, an adjudication
— don't trust one judge's self-report. Aggregate independent votes with a deterministic gate.
Three adjudication gates package this and share the same stop-primitive family:

- [`adversarial-refute-vote`](../skills/adversarial-refute-vote/) — put a claim to N
  independent *refute* votes; a strict majority of refutations stops adoption.
- [`judge-rubric-vote`](../skills/judge-rubric-vote/) — a majority verdict stands only when
  the agreement rate clears a threshold; a split panel is `hung` and **fails** (it exits
  non-zero, and "no votes" is `observe`=exit 2, never a fail-open 0).
- [`fanout-verify-synth`](../skills/fanout-verify-synth/) — a fanned-out set of claims may be
  synthesized only when *every* claim is verified with enough evidence; any refutation
  escalates to a human, and a bare `verified` label without sources is demoted (a label is
  self-report; evidence is data).

The LLM produces the votes; the gate decides. Each treats "no valid votes" as abstain, never
as consent — the same *empty output ≠ zero* discipline the rest of this profile is built on.

## Adoption checklist

0. **a11y gate (any repo with a UI)** — run
   `python3 scripts/a11y-static-check.py --root .`; if it already reports findings, freeze them with
   `--baseline .a11y-baseline.json --update-baseline`, commit the baseline, and add the CI step from
   [`examples/ci/a11y-gate-pattern.md`](../examples/ci/a11y-gate-pattern.md). Exit 3 = zero files
   scanned = UNKNOWN, not a pass.
1. **Hooks (local, fail-closed)** — copy [`examples/hooks/`](../examples/hooks/) into
   `.claude/hooks/` (`pre-bash-safety.sh` + `_strip-command.awk` for destructive commands,
   `pre-file-protect.sh` for `.env`/keys/`.git`/settings), wire into
   `.claude/settings.json`, and keep the self-test green (`bash test-hooks.sh`).
2. **CI summary gate** — add the
   [`all-checks-pass` summary job](../examples/ci/all-checks-pass-pattern.md) to your CI
   and make it the only required status check (free-tier branch protection JSON included).
3. **Secrets & size guard (pre-commit)** — enable
   [`examples/git-hooks/`](../examples/git-hooks/) (`git config core.hooksPath …`) to block
   committed credentials, oversized files, and `.env` before they reach history; every check
   has an allowlist escape hatch (`# pragma: allowlist …`) so a false positive is one comment
   away, not a config war.
4. **Observe, then promote** — log advisory-hook fires (JSONL) and review monthly:
   2+ true positives of one kind → promote to blocking; mostly false positives → tune.

## KPIs worth tracking monthly

- Natural-language-only rules remaining (should trend down)
- Required status checks across repos (should trend up)
- Advisory → blocking promotions (with dates)
- `counts.error` in each repo's `.a11y-baseline.json` (should trend down, never up)
