---
name: project-standards
description: <PROJECT_NAME> プロジェクト固有の開発規約・ドメイン知識・スタック特化ルール。kit 提供の dev-standards を補完する。各 agent 起動時に preload される。
---

# <PROJECT_NAME> Project Standards

> このファイルを `.claude/skills/project-standards/SKILL.md` として配置すると、kit の 4 agent（dev-explorer / dev-planner / dev-tester / dev-reviewer）が起動時に自動で preload する。
> ファイルが存在しない場合、kit は warning を debug log に出すのみで動作には影響しない（公式仕様）。

---

## 1. プロジェクト概要

- **プロジェクト名**: <PROJECT_NAME>
- **目的**: <1-2 sentences>
- **ステータス**: <例: アクティブ開発 / 安定運用 / 内部ツール / プロトタイプ>
- **重要な制約**: <例: 第三者審査・監査対応中で本番影響変更を避ける / GDPR 対象データを扱う>

## 2. スタック

| 層 | 技術 | 備考 |
|---|---|---|
| Frontend | <例: Flutter 3.x / Next.js 15> | <version 縛りや慣習> |
| Backend | <例: Supabase / AWS Lambda> | |
| データ | <例: Postgres + RLS> | |
| 認証 | <例: OAuth 2.0 / SSO> | |
| テスト | <例: Vitest + Playwright> | カバレッジ目標 80% |

## 3. 開発コマンド

```bash
# 依存インストール
<例: pnpm install / pub get>

# 開発サーバー起動
<例: pnpm dev / flutter run>

# テスト
<例: pnpm test / flutter test>

# Lint
<例: pnpm lint / flutter analyze>

# 型チェック
<例: pnpm typecheck>

# ビルド
<例: pnpm build>
```

## 4. ディレクトリ構造の慣習

```
src/                # source
  features/         # 機能別フォルダ（vertical slice）
  shared/           # 横断ユーティリティ
  generated/        # コード生成物（編集禁止）
test/               # 単体テスト
e2e/                # E2E テスト
```

→ 新機能は `features/<name>/` 配下に追加。`shared/` は 2 features 以上で再利用が確定してから。

## 5. プロジェクト固有のルール

### コーディング
- <例: 状態管理ライブラリは X を使う・他と混在させない>
- <例: state は immutable で表現>
- <例: 認証チェックは middleware/guard 層で・component 内では行わない>

### セキュリティ・コンプライアンス
- <例: ユーザー個人情報はサーバ送信前にマスキング>
- <例: 第三者 AI サービス利用箇所は consent modal 必須>

### テスト
- <例: スナップショットテストは UI 変更時に必ず更新>
- <例: E2E は auth flow 全体を 1 ケース必ず通す>

### コミット
- <例: scope は features/ 名と一致させる: `feat(auth): ...`>
- <例: schema 変更は migrations/ も同 commit に含める>

## 6. ドメイン知識

### ビジネスロジックの中核
- <例: ユーザーは 1 つの組織に所属する>
- <例: トランザクションデータは 1 user 1 day = 1 record（複数登録時は upsert）>

### 外部依存
- <例: 外部 API X — レート制限が分間 60>
- <例: 決済プロバイダー Y — 本番接続は環境変数で切替>

## 7. やらないこと（明示的な non-goals）

- <例: 特定プラットフォーム対応は v2 以降>
- <例: 複数 device 間の sync は Phase 3 以降>
- <例: Web 版は当面なし>

## 8. dev-reviewer 向けメモ

このプロジェクトでよくある指摘パターン:
- <例: 外部 API 呼出時の権限 request 漏れ>
- <例: DB 認可ポリシーが schema 追加に追従していない>
- <例: i18n 文字列を直接埋め込み — l10n 経由で>

→ dev-reviewer は memory: project で `.claude/agent-memory/dev-reviewer/MEMORY.md` を育てる。最新指摘パターンはそちらに蓄積される（このファイルは教科書的な初期セット）。

---

## 使い方

1. このファイルを **コピー**して `<your-project>/.claude/skills/project-standards/SKILL.md` に配置
2. `<PROJECT_NAME>` 等のプレースホルダを実値に置換
3. プロジェクト初日に書ける範囲で埋め、開発を進めながら継続的に追記
4. agent が「project-standards から ... を参照しました」と言及するようになれば導入成功
