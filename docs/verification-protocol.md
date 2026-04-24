# 検証プロトコル

## 目的

kit 導入前後の効果を定量比較し、全プロジェクト展開の Go/No-Go 判断材料とする。

## 検証フレーム

スタック・タスク特性が異なる **2 プロダクト並列**で検証することを推奨する。1 プロダクトだけでは kit の汎用性が確認できない。

| 観点 | 推奨 |
|---|---|
| プロダクト数 | 2 本（最低）〜 3 本（理想） |
| スタック多様性 | 主軸スタック × 補助スタック（例: Flutter × Next.js） |
| タスク種別 | bug fix / 小機能追加 / refactor / docs を混ぜる |
| 期間 | 2 週間程度 |
| タスク数 | 各プロダクト 2-3 件、計 5-6 件 |
| リスク管理 | 本番 release 直結タスク・第三者審査直結変更は除外 |

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

直近 5 件の同種タスクの開発記録から逆算:

```
ベースライン例:
- メインセッション tool call 数: 平均 XX
- CONDITIONAL/FAIL 率: XX %
- context 60% 到達時間: 平均 XX 分
```

## 記録テンプレ（タスクごとに 1 ファイル）

各タスク完了時に以下を `<project>/.claude/kit-verification/<YYYY-MM-DD>-<task>.md` 等に記録:

```markdown
# kit verification: <task name>

## メタ
- date: YYYY-MM-DD
- product: <project-id>
- task type: bug fix | feature | refactor | docs
- kit version: 0.1.0

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

## 学び
- 良かった点: ...
- 課題: ...
- kit 改善提案: ...
```

## Go/No-Go 基準

| 指標 | Go ライン | No-Go ライン |
|---|---|---|
| メインセッション tool call 数 | 現状比 **-20% 以上** | 同等 or 増加 |
| CONDITIONAL/FAIL 率 | 現状比 **-30% 以上** | 同等 |
| context 60% 到達時間 | **1.3 倍以上** に延長 | 同等 |
| dev-reviewer MEMORY.md | 5 件以上の有用パターン蓄積 | 0-2 件 |
| 致命的失敗 | **0 件** | 1 件以上 → 即停止 |

- Go なら全プロジェクトへ展開
- No-Go なら原因分析 → kit を v0.2.x で改善 → 検証再実施
