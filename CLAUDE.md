# CLAUDE.md — willink-claude-kit 開発ガイド

> 本 repo を **開発・保守する** Claude Code / Codex / Antigravity 向けの single source of truth。
> 外部利用者（kit を導入する側）向けの説明は [README.md](README.md) を参照。本ファイルは
> 内部開発者・AI agent が **この repo 自体を編集する** ときの規約に絞る（README と内容を重複させない）。

## プロジェクト概要

willink-claude-kit は、Claude Code / Codex / Antigravity で同等の開発パフォーマンスを得るための
標準開発エージェント基盤。**Claude Code plugin を正本（canonical）**とし、他環境は adapter skill +
hash 同期で追従する。提供物の一覧・導入手順は README を参照。

## アーキテクチャ

```
.claude-plugin/        Claude Code plugin entrypoint（正本）
  plugin.json          core metadata（name/version/description）
  marketplace.json     marketplace 定義
.codex-plugin/
  plugin.json          Codex entrypoint（plugin.json の core metadata と同期必須）
codex/sync-manifest.json   Codex 側同期用 hash manifest
agents/                4 サブエージェント（dev-explorer / dev-planner / dev-tester / dev-reviewer）
skills/
  dev-standards/       スタック非依存の汎用標準（正本）
  codex-build/         /build を Codex に適応する adapter skill
  antigravity-build/   /build を Antigravity に適応する adapter skill
commands/build.md      5 phase 版 /build フロー（正本）
examples/              project-standards 雛形・memory seed・prompt テンプレ
scripts/check_sync.py  正本 ↔ adapter の hash 同期チェック / 更新
docs/                  導入ガイド・検証プロトコル・stack 別注意・failure modes
```

**正本と adapter の関係**: `agents/` `skills/dev-standards/` `commands/build.md` `.claude-plugin/`
が正本。これらを変更したら Codex / Antigravity 側 adapter と manifest が古くなるため、必ず同期を取る（後述「テスト」）。

## 開発フロー

本 repo の変更も kit 自身の 4 agent / `/build` で回す（dogfooding）。

- **dev-explorer**（read-only）— 変更前のコードベース調査・影響範囲特定
- **dev-planner**（read-only）— 実装方針の設計。複数ファイルにまたがる変更で起動
- **dev-tester**（write-allowed）— テスト・検証の実行
- **dev-reviewer**（read-only）— Generator-Verifier 分離のレビュー段。実装メインとは別 context で telephone game を回避

小規模な単一ファイル変更（docs 1 箇所の修正等）は agent を起動せず直接編集してよい。

- コミットは **Conventional Commits**（`feat:` / `fix:` / `docs:` / `chore:` 等）
- `agents/` `skills/` `commands/` `.claude-plugin/` を変更したら、同コミット内で manifest を再生成して含める（後述）
- 配布（tag push / GitHub Release / main への直接配信）は原則 **human-only**。AI agent は実施しない
  - **例外: 緊急パッチ**。既に配布済みの版が利用者に実害を与えている場合に限り agent の実施を許可する
    （silently 無効化・インストール不能・セキュリティ問題など。→ [failure-modes #11](docs/failure-modes.md) が実例）
  - ただし **緊急かどうかを agent が自己判断してはならない**。人がそのセッションで明示的に指示した場合にのみ実施する。
    自己申告による例外適用は禁止（`/pulse` の「自己申告禁止」と同じ規律 — 例外の発動条件を agent 自身に
    握らせると、例外が規則を飲み込む）
  - 緊急パッチでも **patch / minor に限る**（major は必ず人手）。CI 全 green が前提
  - 実施後は「何を・どの tag で配布したか」を必ず報告する

## テスト

本 repo のテストは「正本と adapter / manifest の同期が崩れていないこと」の検証が中心。

```bash
python3 scripts/check_sync.py --check    # 同期チェック（CI 相当・exit code で合否）
python3 scripts/check_sync.py --update   # 正本変更を adapter / manifest に反映
```

正本ファイルを変更したコミットは `--check` が exit 0 であること（= manifest 反映済み）を満たす。

加えて kit の挙動そのものは、社内プロダクトでの **14 日検証ログ**（追記式の検証記録）と連動して
品質を担保している。検証指標と記録テンプレは [docs/verification-protocol.md](docs/verification-protocol.md)、
stack 別の検証到達度（✅ verified / 🟡 install-only）は [docs/known-stack-coverage.md](docs/known-stack-coverage.md) を参照。

## 事業コンテキスト（OSS 配信前提）

本 repo は i-Willink が **OSS として外部配信**するプラグインキット（MIT）。public repo であることを前提に:

- issue / PR / commit / コード本体に **顧客名・契約情報・インフラ ID・社内専用パス**を書かない
- 社内固有のドメイン知識は本 repo に置かず、各プロジェクトの `.claude/skills/project-standards/` で拡張する（雛形は `examples/project-standards-template/`）

## 検証フェーズ ステータス

- **14 日検証（完了）**: 2026-04-26 → 2026-05-10 に社内 4 プロダクトで並列実施・完了。結果は CHANGELOG `[1.0.0]` と [docs/known-stack-coverage.md](docs/known-stack-coverage.md)
- **到達状況**: 2 / 4 target stacks が ✅ verified（Flutter+Firebase / Next.js+pnpm）、残り 2 stack は 🟡 install-only
- **現フェーズ**: Q3 dogfood による install-only 2 stack の promotion（基準は known-stack-coverage の Promotion plan）+ 回帰スイートの自走改善ループ。v1.1 繰り越し指標（MEMORY ≥ 5 / context 1.3x）の判定を継続
- **現行リリース**: `.claude-plugin/plugin.json` を正とする（volatile な版番号は本ファイルに直書きしない）
