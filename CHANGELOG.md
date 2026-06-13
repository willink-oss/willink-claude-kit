# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [SemVer](https://semver.org/).

## [Unreleased]

### Added
- `scripts/check_sync.py` に release 整合性チェックを追加 (#10) — CHANGELOG 最新 release ヘッダと README / adoption docs のバージョン pin 例（`"willink-claude-kit@iwillink": ["x.y.z"]`）が plugin.json の version と一致するかを検証。既存 CI の `--check` がそのままガードする。過去 CHANGELOG エントリと array-vs-string の schema 反例（`["0.1.1"]` 等）は許容して false positive を回避

### Fixed
- `docs/adoption-guide.md` のバージョン pin 例が `0.1.1` のまま残っていた不整合を `2.0.0` に修正 (#10)

### Changed
- WordPress/PHP の対象スタック表現を整理 (#11) — `docs/known-stack-coverage.md` で WordPress+PHP を「explicitly out of scope」から「📝 documented, unverified」へ再定義し、利用者が期待できる支援レベル（agent 規約・dev-tester ゲート・standards は documented だが end-to-end 未検証 = best-effort）を明文化。`docs/stack-specific-notes.md` の WordPress 節冒頭に同 status の caveat を追記。coverage の SOP 参照に含まれていた内部パスは除去
- `docs/verification-protocol.md` を v1.0 後の継続評価手順に更新 (#13) — 0.1.x 採用判断の基準・フレームは「歴史的経緯」に凍結し、v1.1 繰り越し指標（MEMORY ≥ 5 / context 1.3x / 致命的失敗 0）と stack promotion の記録様式を現行化。promotion 基準の正本は `docs/known-stack-coverage.md` のまま（重複定義しない）
- dev-reviewer memory の path と auto-write 条件を実態に合わせて整理 (#12) — 正本は `.claude/agent-memory/dev-reviewer/MEMORY.md`（全プラットフォーム共有）のまま、plugin install 時の auto-write が plugin 名前空間付き `.claude/agent-memory/willink-claude-kit-dev-reviewer/` に着地する実測事実と発火条件（`/build` 内のみ・standalone `/agents` では発火しない = 既知制約）を README / agent prompt / adoption guides に明文化。consolidation 手順を adoption-guide §3.2 に追加

## [2.0.0] - 2026-06-11

**Status**: マルチプラットフォーム基盤へ — Claude Code (正本) / Codex / **Antigravity** の 3 環境対応。

### Added
- `skills/antigravity-build/` — Antigravity 向け 5 phase adapter skill
- `docs/antigravity-adoption-guide.md` — Antigravity 導入手順
- `scripts/check_sync.py` — adapter 同期チェックの汎用化 (プラットフォーム非依存)

### Changed
- README をマルチプラットフォーム前提に更新 (Claude Code plugin が正本・各環境は adapter で追従)
- `scripts/check_codex_sync.py` は後方互換 wrapper 化 (`check_sync.py` に委譲)

### Fixed
- 配布 metadata のバージョン不整合を解消 (#9) — plugin.json / marketplace ref / docs を 2.0.0 で統一


## [1.0.0] - 2026-05-10

**Status**: 🟡 **Partial Go** — verified production-ready on 2 of 4 target stacks. The remaining 2 stacks ship in install-only mode pending Q3 dogfood data.

### Summary

Graduates from **0.x unstable → 1.0** based on a 14-day verification across i-Willink internal products (`tsuu`, `fit-ai`, `clubhouse`, `clublink-platform`). Full Go criteria were partially met: zero fatal failures, all six install-time pitfalls fixed upstream, and dev-reviewer's batch-triage output drove a real production merge. Two indicators (MEMORY ≥ 5 entries, context-budget extension ≥ 1.3×) remain unverified due to time constraints, not kit defects — they are deferred to v1.1 evaluation.

See `docs/known-stack-coverage.md` for the per-stack matrix.

### Added

- `docs/known-stack-coverage.md` — per-stack verification matrix (Flutter+Firebase / Next.js+pnpm verified; Flutter+Supabase / Next.js+Stripe install-only).
- v1.0 release note acknowledging the 14-day dogfood program (2026-04-26 → 2026-05-10).

### Verified during 0.1.x → 1.0

- **Stack-agnostic dev-reviewer baseline** matches manual-preload review on Firestore + Cloud Functions (HIGH-severity findings 100% overlap, LOW 75%).
- **Cross-cutting batch triage** value: dev-reviewer's recommended merge order across 4 dependabot PRs (`#1859 → #1858 → #1860 → #1857`) was followed verbatim during same-day production merges, capturing a lockfile ordering risk that single-PR review consistently misses.
- **`/build` full flow** completes a docs task end-to-end: Phase 1/2 auto-skip, Phase 4 dev-tester ∥ dev-reviewer truly parallel (~42% wall-clock reduction vs serial), Phase 5 commit succeeds.
- **MEMORY auto-write triggers inside `/build`** (3 entries created in `.claude/agent-memory/willink-claude-kit-dev-reviewer/`). Standalone `/agents` invocations do **not** trigger MEMORY writes — see `docs/verification-protocol.md` for the operational implication.
- **Co-existence with existing project agents**: in repos with prior agents (e.g. `architecture-validator`, `security-reviewer`), kit's `dev-*` family runs side-by-side without naming or hook collisions. Roles are complementary (kit = stack-agnostic / breadth, existing = stack-specific / depth).

### Fixed (install-time pitfalls — all surfaced by dogfood)

- `enabledPlugins` value typing: docs corrected to use the schema-valid `["1.0.0"]` array form (string `"0.1.0"` is rejected by Claude Code's `settings.json` validator).
- `.gitignore` collision: adoption guide step 3 now points consumers with a pre-existing `.claude/agent-memory/` ignore rule to `.claude/agent-memory-local/`.
- `marketplace.json source` schema: switched from `source: "."` (unsupported) to `source: "url"` (full-repo clone).
- `claude plugin tag` step required for `/agents` to discover kit-provided agents.
- `enabledPlugins: <plugin>: true` boolean form documented as the install trigger (vs array, which only pins).
- README adoption ordering and copy-paste blocks aligned with the working flow.

### Stability commitment

- The 4 standard agents (`dev-explorer`, `dev-planner`, `dev-tester`, `dev-reviewer`) and the 5-phase `/build` flow are the stable surface.
- `examples/project-standards-template/` continues to evolve; its shape may still change in 1.x minors.

### Known limitations carried into 1.0

- **Stack-specific lint / architecture rules are not in `dev-standards`.** Consumers must extend `project-standards/SKILL.md` to capture project-local conventions (e.g. Flutter Riverpod placement). kit's stack-agnostic baseline alone will not flag those.
- **MEMORY auto-write does not fire for standalone `/agents` invocations.** Enabled inside `/build` only. Tracked in `docs/verification-protocol.md` and pending upstream decision (intentional vs bug).
- **Two of the four target stacks (Flutter+Supabase, Next.js+Stripe) have not been exercised end-to-end yet.** They are install-only at 1.0; promotion to fully verified is gated on Q3 dogfood data.

### Migration from 0.1.x

- No code-side breaking changes. Bump `enabledPlugins["willink-claude-kit@iwillink"]` to `["1.0.0"]` (or `true` for floating).
- Repos that adopted 0.1.x with the documented array form continue to work without modification.



## [0.1.1] - 2026-05-06

### Added

- Codex plugin entrypoint (`.codex-plugin/plugin.json`) that mirrors Claude plugin core metadata.
- `skills/codex-build/` adapter skill for running the 5 phase build flow in Codex.
- Codex adoption guide and Claude-to-Codex sync check with CI workflow.

## [0.1.0] - 2026-04-24

### Added

- Initial scaffold: 4 standard agents (`dev-explorer`, `dev-planner`, `dev-tester`, `dev-reviewer`)
- `skills/dev-standards/` — stack-agnostic baseline
- `commands/build.md` — 5 phase build flow
- `examples/project-standards-template/` — domain-knowledge extension scaffold for downstream repos
- Plugin metadata (`.claude-plugin/plugin.json`, `marketplace.json`)
- MIT license

### Notes

- Status: **unstable**. Verification ongoing on internal products.
- Designed for Opus 4.7 (`model: inherit` throughout).
