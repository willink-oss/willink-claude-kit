# Harness profile — deterministic gates for AI-driven repos

A minimal, proven set of *deterministic* enforcement layers for repositories where an AI
coding agent does most of the work. Telling the agent "follow our standards" is
probabilistic; a gate that exits non-zero is not. This profile packages what the kit's
home organization enforces in production (ADR-019).

## The ladder

| Layer | What | Where in this kit |
|---|---|---|
| H1 | Docs / natural-language rules | `CLAUDE.md`, `examples/project-standards-template/` |
| H2 | AI semantic review | PR review agents (advisory) |
| H3 | **Blocking verification** | hooks ([`docs/hooks-guide.md`](hooks-guide.md), [`examples/hooks/`](../examples/hooks/)) + CI required checks ([`examples/ci/`](../examples/ci/)) + deterministic gates (`skills/` coverage-floor-lock · token-codegen-gate · commit-convention-gate · self-heal-ci) |
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
6. Read live state before you report it. Status / progress / "is it deployed?" claims must
   come from a live probe, not a document (docs are *plan*; live is *state*). The read-only
   [`/pulse`](../commands/pulse.md) command is the measurement layer for this profile — it
   renders only what a deterministic probe returned and writes `❓` when a probe fails, so a
   green dashboard cannot be hallucinated.

## Autonomous loops

An agent that loops until "done" must not decide "done" itself. Bound every autonomous loop
with a **deterministic stop check** and a **hard attempt cap**. The
[`/goal-loop`](../commands/goal-loop.md) command wraps `scripts/goal-loop.sh` for exactly
this: it stops only when a `--check` command exits 0 (green tests, coverage ≥ T, lint = 0)
and caps retries so the loop always terminates — no self-reported "I think it's done".
`maker-checker-relay` (`skills/maker-checker-relay/`) extends it to a Generator↔Verifier
loop — implementer vs. read-only reviewer — that completes only when tests are green AND the
reviewer has zero blocking findings.

## Adoption checklist

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
