# 典型プロンプトテンプレート

`/build` 起動時の参考プロンプト。タスク特性に応じて使い分ける。

## 1. 小規模 bug fix

```
/build

src/auth/login.ts の login() で email が undefined のとき throw する。
バリデーション漏れ。Phase 1/2 は skip して直接修正してほしい。
```

→ メインが Phase 3 直行 → Phase 4 並列検証 → コミット

## 2. 新機能（中規模）

```
/build

users テーブルに `display_name` カラムを追加し、profile 編集 UI から更新できるようにする。
- migration / API / UI の 3 軸が独立 → Phase 1 で 3 並列 explore してほしい
- Supabase RLS の更新も必要
```

→ Phase 1 で dev-explorer × 3 → Phase 2 で dev-planner → Phase 3 実装 → Phase 4 並列検証

## 3. リファクタリング

```
/build

apps/web の auth context が肥大化してきた。features/auth/ 配下に分離したい。
- 影響範囲が広い → Phase 1 で「現状の auth 利用箇所」を調査してほしい
- 振る舞いを変えない refactor として進める（test 追加なし、既存 test が pass すること）
```

→ Phase 1 起動 → Phase 2 起動（refactor 計画）→ Phase 3 実装 → Phase 4 検証（既存 test が green を維持しているかが主眼）

## 4. ドキュメント更新

```
/build

README.md の「導入手順」が古い。現在の v0.3 の構成に合わせて更新してほしい。
全 phase skip で OK。
```

→ メインが直接 Edit → コミット（subagent 起動なし）

## 5. 検証フェーズ専用（実装は別途完了済み）

```
/build

直前の commit (abc123) のレビューだけお願い。Phase 4 から開始。
```

→ Phase 4 のみ起動（dev-tester ∥ dev-reviewer 並列）

## 6. dev-reviewer の memory を確認したい

```
このプロジェクトで過去に dev-reviewer が指摘した recurring pattern を確認したい。
.claude/agent-memory/dev-reviewer/MEMORY.md の中身を要約してほしい。
```

→ メインが直接 Read（agent 起動不要）
