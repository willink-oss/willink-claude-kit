# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **commit/CI 品質ゲート バンドル** — 決定論ゲート 5 種（H3/H4 層）。`coverage-floor-lock`（`scripts/coverage-floor-check.sh`・カバレッジ下限ゲート + floor 引き下げ diff = gaming 検出）・`token-codegen-gate`（`scripts/codegen-drift-check.sh`・config 駆動で生成物を再生成→差分ゼロを非破壊検証: design token/TS types/OpenAPI/protobuf）・`architecture-parity-gate`（`scripts/arch-parity-check.py`・config 宣言の依存方向 + レイヤ命名違反を検出・言語非依存 stdlib）・`commit-convention-gate`（`scripts/commit-convention-check.sh`・Conventional Commits prefix/空虚メッセージ/なぜ欠落を決定論検査）・`self-heal-ci`（`scripts/self-heal-ci.sh`・赤 CI を検知→修正→再検証を緑まで反復・`scripts/goal-loop.sh` の試行上限で必ず止まる・escalation は `--escalate-file` でパラメータ化）。各 skill + backing script + hermetic 自己テスト + CI 回帰テスト（`scripts/test/test_*`・run.sh 自動 discover）。config 駆動ゲートは `examples/` に雛形同梱。全て de-brand 済（org パス/ADR/承認 tier なし）・BSD grep 安全。
- **goal-loop 自律コア** — 決定論的な停止プリミティブ群。`scripts/goal-loop.sh`（`--check` の exit code のみで達成判定し `--max` で必ず止まる。**達成をモデルが自己申告しない**）+ `scripts/goal-loop-template.sh`（新指標向けに配線済みループ雛形を生成）+ `scripts/maker-checker-relay.sh`（Generator=実装 Maker ↔ Verifier=読取専用レビュー Checker〔kit の `dev-reviewer` agent / `/review` / 人〕を分離し「test 緑 かつ BLOCKER 指摘 0」の真理値表ゲートで完了判定）。`commands/goal-loop.md`（組み込み `/goal` を置き換えず決定論 `--check`+cap の規律を上乗せ）+ `skills/maker-checker-relay/`。`scripts/test/test_goal_loop.sh` が停止プリミティブの exit code 契約（0=MET/1=CONTINUE/2=CAP）+ 2 ラッパーの hermetic 自己テスト（生成→bash -n→配線 / 真理値表 7〔malformed pattern の fail-closed 含む〕+ print-check 2）を回帰ロック（ubuntu+macos）。de-brand 済（org パス/ADR/承認 tier なし）・BSD grep 安全。
- **`/pulse` — live-state status コマンド** — `commands/pulse.md`（6 phase）+ `scripts/pulse-precheck.sh`（read-only の live-state Verifier）。プロジェクト現況を deterministic な probe で実測し（git ahead/behind・未 commit WIP・open PR + review 状態・HEAD の CI 結論・tag..HEAD=merged≠deployed・cheap check・stale ブランチ・TODO/FIXME・依存 audit・prod fingerprint=green-while-broken・doc 鮮度）、状態表と上限 5 件の次アクションに落とす。中核不変条件: **文書=plan/live=state**・自己申告禁止（probe 行の裏付け無き状態表現を出さない）・**probe 失敗は `0` でなく `❓`**（"空出力 ≠ ゼロ"）。stack 自動検出（Node/Flutter/Go/Rust/PHP/Python/generic）・gh↔glab・BSD/GNU 両対応。既存 4 agent を再利用し 5 本目を足さない（Phase 4 で dev-explorer/dev-reviewer を read-only 借用）。`scripts/test/test_pulse_precheck.sh` が「probe 失敗→❓（0 でない）」を hermetic に自己テスト（fixture git repo + fake failing gh + `file://`）。`/build` と対の「測る」コマンド。
- **Tier-1 決定論ガードレールの雛形** — `examples/hooks/` に production 版の Claude Code hooks を追加: `pre-bash-safety.sh`（+ `_strip-command.awk`・破壊コマンド denylist・引用/heredoc を剥がしてから走査・jq→python3 fallback で単一 CLI SPOF 回避）・`pre-file-protect.sh`（`.env`/鍵/`.git`/settings ガード）・`post-build-eval.sh`（test/lint/build 失敗の Evaluator advisory）・`pre-compact-snapshot.sh`（/compact 直前の作業状態永続化）・`post-tool-log.sh`（tool 呼出を JSONL 記録＝"observe, then promote" の素材）。`examples/git-hooks/` に git pre-commit gate: `pre-commit-quality.sh`（secret/1MB/`.env` を history 到達前に block・`# pragma: allowlist secret` 逃し）・`pre-commit-shell-lint.sh`（BSD 非互換 `grep -P`/Perl エスケープ + `shellcheck` error を block・kit 自身の移植性ドクトリンを機械強制）+ dispatcher + README。全 hook が block+pass 自己テスト付き（`examples/hooks/test-hooks.sh` 23 assert・`examples/git-hooks/test-git-hooks.sh` 5 assert・CI は `scripts/test/test_hooks.sh` / `test_git_hooks.sh` / `test_pulse_precheck.sh` で ubuntu+macos matrix 実行）。`docs/session-hygiene.md` / `docs/subagent-guidelines.md`（H1 natural-language ルール）と `docs/hooks-guide.md` の "hard-won lessons" 節を追加。`docs/harness-profile.md` の adoption checklist を具体テンプレに接続。
- **harness プロファイル** — `docs/harness-profile.md`（決定論的ゲートの H1-H4 ラダー・deterministic-first 5 原則・導入チェックリスト・KPI）+ `examples/ci/all-checks-pass-pattern.md`（CI summary job を唯一の required status check にするパターン。path-filter monorepo での skipped=satisfied 挙動・`if: always()` の必然性・無料枠 branch protection JSON・実運用の落とし穴を含む。willink-labs 3 リポで実証済み）。ホーム org の ADR-019 に基づく。
- hook 雛形 + 自己テストハーネス + 規約ガイド (#22) — `examples/hooks/` に fail-closed な PreToolUse 例（危険コマンドを `exit 2` でブロック）と fail-open な Notification 例（エラー時は常に `exit 0`）を追加。両者とも stdin JSON を `jq` で解析し、BSD/GNU 両対応の POSIX ERE（`grep -E`、`grep -P` 不使用）。`examples/hooks/test-hooks.sh` が block/pass 両ケースを自己テスト（フォルダごとコピー可能な自己完結型）。`scripts/test/test_hooks.sh` で既存スイートに統合し、CI を ubuntu + **macos** matrix 化して BSD grep 移植性を実機で実証。規約は `docs/hooks-guide.md`（fail-open/closed・stdin JSON・grep 移植性・有効イベント名一覧）に集約。
- 回帰 / 整合テストスイート (`scripts/test/`) + runner (`run.sh`) を新設。kit の不変条件（adapter sync・plugin/marketplace manifest 妥当性・agent guard 句の verbatim 保持・repo 構造・release version pin）をロックする 4 テストファイル（計 43 アサーション）。`.github/workflows/test.yml` で push/PR 時に実行（`codex-sync.yml` は同期特化のまま棲み分け）。テスト常時改善・失敗→ソース改善 issue 化の自走ループ（crew `loop-test-cycle`）の土台。

## [2.1.0] - 2026-06-13

**Status**: ループ開発サイクル（ADR-009）が自律生成した初のリリース。dev サイクル 4 件（kit #13/#12/#11/#10）を Maker-Checker 分離 + 人間レビューで取り込み、deploy ステージで切り出した。docs 整合の徹底 + check_sync への release 整合性ガード追加が主軸。

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
