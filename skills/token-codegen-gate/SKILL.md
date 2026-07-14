---
name: token-codegen-gate
description: Config-driven codegen drift gate. Runs a generate command and verifies the regenerated artifacts equal the committed (tracked) files with zero diff, non-destructively. Works for design tokens, TS types, OpenAPI clients, protobuf, and any codegen. Exits 1 on drift. Triggers: codegen drift, codegen gate, generated artifact diff, generate drift, git diff exit-code, regenerate check, DS token codegen, codegen parity.
allowed-tools: Bash, Read
---

# token-codegen-gate (non-destructive codegen drift gate, config-driven)

> Deterministically verify that "running the generate command reproduces the
> **committed artifacts** (the tracked files in the working tree) with zero diff".
> Design-token CSS/TS is the obvious case, but it applies to any codegen — TS type
> generation, OpenAPI clients, protobuf — via a **config** file.
> It performs the classic CI pattern `<generate> && git diff --exit-code`, but
> **without depending on git state** — it targets only the declared artifacts and is
> **non-destructive** (the working tree is left unchanged before/after generation).
> Drift trips the gate with **exit 1**.

## Why it matters

- If you hand-edit a generated artifact (e.g. a DS token CSS/TS file), or change the
  source of truth but commit without regenerating, the invariant "**artifact == source**"
  breaks (drift).
- Drift is the gap between "defined" and "the actual output matches", and a **self-report
  cannot detect it**. This gate regenerates, **mechanically compares** the diff, and rejects
  drift with exit 1.

## Approval / scope — applying it is a manual step

- **This skill and `${CLAUDE_PLUGIN_ROOT:-.}/scripts/codegen-drift-check.sh` only inspect**
  (generate -> compare -> restore). They do **not** run any fix on drift (regenerate /
  commit / revert-edit are manual).
- **Non-destructive guarantee**: the declared artifacts are backed up to a temp dir before
  generation and **always restored** afterward. If generation created an output that did not
  exist before, only that **single declared file** is removed (`rm -f` — never `rm -rf` /
  directory removal). So the working tree is unchanged before/after.
- **Wiring the gate into CI / a hook is a manual step** — this skill does not add it to a CI
  job or turn it into a pre-commit check.
- **Making it a required status check on a protected branch is a self-lockout risk** — get
  human approval first (a misconfigured required gate can block your own merges).
- git / hooks / settings are never modified.

## How to run

1. Config-driven (config-driven, non-destructive):
   ```
   bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/codegen-drift-check.sh" --config <path.json>
   ```
   config JSON (no jq dependency; parsed with python3) — see the shipped example at
   `examples/codegen-drift.config.example`:
   ```json
   {
     "workdir": "packages/tokens",
     "targets": [
       { "name": "design tokens", "cmd": "npm run tokens:build",
         "outputs": ["dist/tokens.css", "dist/tokens.ts"] }
     ]
   }
   ```
   - workdir precedence: `target.workdir > config.workdir > --workdir > "."`.
   - Relative output paths resolve against each target's workdir. **outputs are files only**
     (directories are unsupported).
2. Ad-hoc (no config; single target):
   ```
   bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/codegen-drift-check.sh" --cmd "<generate command>" \
     --output <file> [--output <file> ...] [--workdir <dir>]
   ```
3. Verdict and exit codes:
   - **exit 0** = all targets IN-SYNC (artifact == regenerated result, no drift).
   - **exit 1** = one or more DRIFT (gate trips). Do the printed fix (regenerate -> `git add`
     -> commit, or revert the edit) **by hand**, then re-run the gate and confirm IN-SYNC.
   - **exit 2** = bad argument / config schema (e.g. an output is a directory).
   - **exit 3** = cannot verify = **unknown** (generate command failed / config unparseable /
     python3 missing). Do not read unknown as 0 findings (an empty output is not a pass).

## Deterministic --self-test

```
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/codegen-drift-check.sh" --self-test
```
- **exit 0** = the core `check_target` (backup -> generate -> compare -> restore) and config
  extraction (`cfg_py`) behave as expected across all in-sandbox fixture cases: in-sync -> 0 /
  drift -> 1 / new artifact -> 1 / generate failure -> 3 / multi-output with one drift -> 1 /
  output=dir -> 2 / config unparseable -> 3, **and the working tree is restored in every
  case** (non-destructive).
- **exit 1** = a mismatch in any case (logic broken).
- The self-test is **hermetic** (only a fake generator and fake artifacts inside a sandbox —
  no external repo / network / git). It is not hard-coded success: it actually calls the same
  `check_target` used in real mode and checks the outputs and the restored state (no
  self-report).

## When to use / not

- Use it when a generated artifact is committed to the repo and you want to guarantee it stays
  equal to what its generator produces (DS tokens, TS types, OpenAPI/protobuf clients).
- Not for a one-off manual check with no repeatable generate command.

## Related

- primitive: `scripts/goal-loop.sh` (deterministic stop check + attempt cap) — pair this gate's
  `--check` exit code with a goal-loop when driving a regenerate-until-in-sync loop.
- script: `scripts/codegen-drift-check.sh` (`--config` / `--cmd`+`--output` / `--self-test`)
- Review role: the kit's `dev-reviewer` agent (read-only), a `/review` session, or a human.
