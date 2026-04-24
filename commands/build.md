---
description: i-Willink 標準開発フロー（5 phase）— 探索→計画→実装→並列検証→修正/コミット。Generator-Verifier 分離・並列探索・project-standards 拡張に対応。
allowed-tools: Read, Glob, Grep, Edit, Write, Bash, Agent, WebFetch
---

# /build — 5 Phase Development Flow

タスクを 5 phase で進める。各 phase の subagent 起動は **「subagent コスト > 利益」のとき skip** する（小修正・typo は Phase 3 直行）。

## Phase 1: 影響範囲探索（dev-explorer × N）

**起動条件**: タスクの影響範囲が **3 軸以上独立**（例: backend API + DB schema + frontend）。1-2 軸なら直接 Read/Grep。

```
3 つの dev-explorer を並列起動：
  1. <area A> — <scoped question A>
  2. <area B> — <scoped question B>
  3. <area C> — <scoped question C>
```

各 explorer は read-only。最大 3 並列（Token cost 3-10x を抑制）。

## Phase 2: 実装計画（dev-planner × 1）

**起動条件**: 変更が >1 file または >50 行。typo / 1 関数の bug fix なら skip。

```
dev-planner に Phase 1 の結果と要件を渡し、実装計画を受け取る。
```

返ってくる計画には: ファイル別変更内容 / 既存ユーティリティ再利用 / 実装ステップ / テスト戦略 / ロールバック手順。

## Phase 3: 実装（メイン Claude）

**メイン Claude が Edit/Write で実装する**。subagent には委譲しない（Generator-Verifier 構造を保つ・差分の責任所在を明確化）。

実装中の指針:
- 計画に従う（逸脱する場合は理由を明示）
- 既存ユーティリティを再利用
- 過剰なエラーハンドリング・防御コード追加を避ける
- 関連しない refactor を混ぜない

## Phase 4: 検証（dev-tester ∥ dev-reviewer）

**並列起動**:

```
dev-tester:    test / lint / typecheck / build を full run → PASS/PARTIAL/FAIL
dev-reviewer:  diff を読取専用レビュー → PASS/CONDITIONAL/FAIL
```

両者の判定マトリクス:

| dev-tester | dev-reviewer | 次の行動 |
|---|---|---|
| PASS | PASS | Phase 5 → commit |
| PASS | CONDITIONAL | Phase 5 → 指摘修正 → 再 Phase 4 |
| PARTIAL/FAIL | * | Phase 5 → テスト/型エラー修正 → 再 Phase 4 |
| * | FAIL | 設計から見直し → Phase 2 へ戻る |

## Phase 5: 修正 + コミット

**修正はメイン Claude が実施**（dev-fixer は定義しない方針）。

- Phase 4 の指摘を 1 件ずつ修正
- 再 Phase 4 を最大 **2 ループ** まで（3 回目で打ち切り → CEO 判断）
- 全 PASS になったら Conventional Commits でコミット
- 大きな差分は論理単位で分割コミット

```
git commit -m "<type>(<scope>): <subject>

<WHY を本文で>"
```

---

## subagent skip 判断早見表

| タスク特性 | Phase 1 | Phase 2 | Phase 4 |
|---|---|---|---|
| typo 修正 | skip | skip | dev-tester のみ |
| 1 関数 bug fix | skip | skip | 並列 |
| 新機能（小） | skip | 起動 | 並列 |
| 新機能（大） | 起動 | 起動 | 並列 |
| リファクタ | 起動 | 起動 | 並列 |
| ドキュメント | skip | skip | skip |

---

## 失敗モード対策（公式ブログ準拠）

- **Early victory**: dev-tester は full suite 完走必須
- **Telephone game**: Phase 3/5 を subagent 化しない
- **Options flooding**: 4 agent に厳選（追加禁止）
- **Subagent コスト爆発**: Phase 1 並列は 3 軸以上独立な時のみ
- **同一ファイル並列編集**: Phase 4 は read-only agent のみ並列

---

## 関連

- agents/ — dev-explorer / dev-planner / dev-tester / dev-reviewer 定義
- skills/dev-standards/ — 共通開発標準
- docs/failure-modes.md — 失敗モード詳細
- docs/adoption-guide.md — 導入手順
