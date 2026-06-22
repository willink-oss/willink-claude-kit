# 検証プロトコル

> v1.0 リリース（2026-05-10・partial Go）までの 0.1.x 採用判断は本書の旧版で実施・完了済み。
> 本書は **v1.0 以後の継続評価**（v1.1 評価 + stack promotion）の現行手順。
> 0.1.x 当時の判断基準と結果は末尾「歴史的経緯」と CHANGELOG `[1.0.0]` を参照。

## 目的

v1.0 partial Go で確定した採用判断を前提に、以下 2 種類の評価を記録する:

1. **v1.1 継続評価** — v1.0 で時間制約により未検証のまま繰り越した 2 指標の判定
2. **stack promotion（🟡 install-only → ✅ verified）** — `docs/known-stack-coverage.md` の
   promotion criteria に基づく昇格の記録

どちらの評価も、本書の記録テンプレ 1 ファイルで記録できる。

## 検証フレーム

v1.0 検証（2 プロダクト並列 × 14 日）で実証済みのフレームを縮小継続する。
新規 stack の promotion は **1 stack = 1 dogfood セッション（〜60 分）** が目安
（`docs/known-stack-coverage.md` の Promotion plan 準拠）。

| 観点 | 推奨 |
|---|---|
| タスク種別 | bug fix / 小機能追加 / refactor / docs を混ぜる |
| リスク管理 | 本番 release 直結タスク・第三者審査直結変更は除外 |
| promotion | 1 stack = 1 セッションで昇格可（記録ファイルの成立が条件） |

## 計測指標

各タスクで以下を記録:

| 指標 | 計測方法 |
|---|---|
| メインセッション tool call 数 | `/cost` または transcript から数える |
| `/build` Phase 4 CONDITIONAL/FAIL 率 | dev-reviewer 出力で判定 |
| context 60% 到達までの時間 | 開始時刻と 60% 通知の時刻差 |
| dev-reviewer MEMORY.md 蓄積件数 | git log でカウント |
| 致命的失敗 | コミット混入バグ・本番影響事例（あれば即停止） |

## ベースライン

v1.0 検証（2026-04-26 → 2026-05-10・14 日間）の実測値をベースラインとする。
旧 0.1.x 検証の「直近 5 件の同種タスクから逆算」は導入前比較（採用判断）用であり、完了済み。

## 記録テンプレ（タスクごとに 1 ファイル）

各タスク完了時に以下を `<project>/.claude/kit-verification/<YYYY-MM-DD>-<task>.md` 等に記録する。
**promotion 用件**（known-stack-coverage が要求する項目）と **v1.1 評価用件**を 1 テンプレで満たす。

```markdown
# kit verification: <task name>

## メタ
- date: YYYY-MM-DD
- product: <project-id>
- task type: bug fix | feature | refactor | docs
- kit version: <インストール中の実バージョン 例: 2.1.0>
- mode: /build full flow | /agents 単発 | review only
- 評価種別: v1.1 継続評価 | stack promotion | 通常記録

## phase 別実行
- Phase 1 (dev-explorer): 起動 / skip — 理由: ...
- Phase 2 (dev-planner): 起動 / skip — 理由: ...
- Phase 3 (main implement): 経過時間 ... 分
- Phase 4 (dev-tester ∥ dev-reviewer): 並列起動 / 結果 ...
- Phase 5 (修正 + commit): 修正ループ N 回

## 計測値
- main session tool calls: ...
- dev-reviewer verdict: PASS | CONDITIONAL | FAIL
- context % at end: ...
- 修正ループ回数: ...
- MEMORY 新規エントリ数: ...

## 摩擦と価値（promotion では各 1 件以上必須）
- 摩擦: ...
- 価値: ...

## upstream issue 候補（空でも「なし」と明記）
- ...

## 学び
- 良かった点: ...
- 課題: ...
- kit 改善提案: ...
```

## v1.1 継続評価指標（現行の判断基準）

v1.0 を **partial Go** とした際に繰り越した未検証 2 指標 + 常時監視 1 項目。
v1.1 評価ウィンドウ（Q3 dogfood）で判定する:

| 指標 | 達成ライン | 未達時 |
|---|---|---|
| dev-reviewer MEMORY.md 蓄積 | 5 件以上の有用パターン | 原因分析 → 次の v2.x minor で改善 → 再評価 |
| context 60% 到達時間 | ベースライン比 **1.3 倍以上**に延長 | 同上 |
| 致命的失敗（常時監視） | **0 件** | 1 件以上 → 即停止（バージョン・評価種別を問わず常時適用） |

stack promotion（🟡 → ✅）の判断基準は本書では定義しない。
**`docs/known-stack-coverage.md` の「Promotion criteria」が正本**であり、本書は記録様式のみを提供する
（2 文書に基準を重複定義しない）。

## 歴史的経緯（0.1.x 採用判断・完了済み）

- 0.1.x 当時の本書は「kit 導入前後の効果を定量比較し、全プロジェクト展開の Go/No-Go 判断材料とする」
  導入判断プロトコルだった
- 2 プロダクト並列 × 2 週間 × 5-6 タスクのフレームで実施し、2026-05-10 に **partial Go**
  （4 stack 中 2 stack verified・致命的失敗 0 件）で v1.0 リリース
- 当時の Go/No-Go 基準（tool call -20% / CONDITIONAL/FAIL 率 -30% / context 1.3x /
  MEMORY 5 件 / 致命的失敗 0）のうち、未検証だった MEMORY と context の 2 指標が
  上記「v1.1 継続評価指標」へ繰り越された。**この旧基準を現在の運用基準として使わないこと**
- 詳細: CHANGELOG `[1.0.0]`・i-willink-crew `assets/knowledge/2026-05-10-claude-kit-validation-report.md`
