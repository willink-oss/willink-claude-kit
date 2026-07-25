---
name: codex-build
description: i-Willink 標準開発フローを Codex で実行するための 5 phase adapter。Claude Code の /build と同期し、Codex subagents はユーザーが明示した場合のみ使う。
---

# codex-build — Codex Adapter For The 5 Phase Flow

This skill adapts the canonical Claude Code `/build` flow in `commands/build.md` for Codex. The Claude Code files remain the source of truth; this skill explains the Codex execution model where platform behavior differs.

## Canonical Inputs

- Read `skills/dev-standards/SKILL.md` before starting substantial work.
- If the task touches UI, also read `skills/a11y-standards/SKILL.md`. Accessibility is decided in Phase 2, not
  audited afterwards.
- If present in a downstream repository, read `.claude/skills/project-standards/SKILL.md` for project-specific conventions. Do not create a separate Codex copy.
- If reviewing a diff in a downstream repository, read `.claude/agent-memory/dev-reviewer/MEMORY.md` when present. Keep that Claude path as shared project memory.
- Treat `commands/build.md` and the four Claude role contracts in `agents/` as canonical:
  - `dev-explorer`
  - `dev-planner`
  - `dev-tester`
  - `dev-reviewer`

## Codex Operating Rules

- Codex stays on the main critical path for implementation and fixes. Phase 3 and Phase 5 are not delegated.
- Use Codex subagents only when the user explicitly asks for sub-agents, delegation, or parallel agent work. If the user did not authorize that, execute the same role behavior locally.
- When subagents are authorized, keep delegated tasks concrete, bounded, and read-only unless the user explicitly asked for parallel implementation with disjoint ownership.
- Prefer `rg` / `rg --files` for search, preserve unrelated user changes, and verify with the repo's actual commands.

## Phase 1: Impact Exploration

Follow the `dev-explorer` contract. Run this phase only when the task spans 3+ independent areas; otherwise inspect locally.

Codex mapping:
- Without explicit subagent authorization: search and read the relevant areas in the main Codex session.
- With authorization: spawn up to 3 `explorer` agents for orthogonal read-only questions, then continue useful local work while they run.

Return only decision-useful context: summary, key files, conventions observed, open questions, and recommended follow-up.

## Phase 2: Implementation Planning

Follow the `dev-planner` contract when the change is non-trivial: more than one file, more than about 50 lines, or meaningful architectural risk.

Codex mapping:
- Build the plan in the main Codex session by default.
- If the user explicitly requested agent delegation, a read-only planning subtask may be delegated, but the main Codex agent owns the final implementation decisions.

The plan must identify files to change, existing utilities to reuse, implementation steps, tests, and rollback path.

For UI work the plan must also state, per new/changed interactive element, its accessible name, role and state,
plus how the layout behaves at 200% text. Write "a11y: N/A (no UI change)" when it does not apply — a blank
section reads as "not considered".

## Phase 3: Implementation

The main Codex agent implements the change. Do not delegate sequential implementation to another agent.

Implementation rules:
- Follow the plan unless new repo facts require a change.
- Keep edits scoped to the task.
- Reuse existing helpers and local patterns.
- Avoid unrelated refactors and speculative abstractions.

## Phase 4: Verification

Run `dev-tester` and `dev-reviewer` behavior after implementation.

Codex mapping:
- Without explicit subagent authorization: run quality gates and perform the code review locally.
- With authorization: verification may run in parallel through read-only agents while the main Codex session handles non-overlapping follow-up work.

Tester behavior:
- Detect the stack from repo files.
- Run all configured quality gates, even when an earlier command fails.
- For UI diffs, also run the a11y static gate
  (`python3 scripts/a11y-static-check.py --root . [--baseline .a11y-baseline.json]`) and the project's a11y
  tests. Exit 3 means zero files were scanned — that is UNKNOWN, never a pass.
- Report PASS, PARTIAL, or FAIL with commands run, failures, skipped tests, and suggested fix scope.

Reviewer behavior:
- Read the full diff and changed files in context.
- Check spec adherence, code quality, error handling, security, accessibility, tests, standards, and scope
  discipline. On UI diffs, a missing accessible name or role is CRITICAL, not a nitpick.
- Report PASS, CONDITIONAL, or FAIL. PASS only when the reviewer would merge it.

## Phase 5: Fixes And Commit

The main Codex agent fixes issues surfaced in Phase 4.

- Address tester/reviewer findings one by one.
- Repeat Phase 4 up to 2 loops when needed.
- Use Conventional Commits if the user asks Codex to commit.
- Split large changes into logical commits when committing.

## Skip Table

| Task type | Phase 1 | Phase 2 | Phase 4 |
|---|---|---|---|
| typo fix | skip | skip | tester only when useful |
| one-function bug fix | skip | skip | tester + reviewer behavior |
| small feature | skip | run | tester + reviewer behavior |
| large feature | run | run | tester + reviewer behavior |
| refactor | run | run | tester + reviewer behavior |
| docs only | skip | skip | skip unless docs validation exists |

## Failure Modes To Guard Against

- Early victory: never treat a partial command run as a pass.
- Telephone game: do not hand off sequential implementation/fix work.
- Options flooding: keep role mapping to the four canonical contracts.
- Same-file parallel edits: only parallelize read-only work unless ownership is explicit and disjoint.
- Context pollution: ask subagents for compact reports, not long transcripts.
