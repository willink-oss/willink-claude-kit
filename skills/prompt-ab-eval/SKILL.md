---
name: prompt-ab-eval
description: プロンプト A/B の 2 スコアセットを同一 eval で採点し、勝者とマージンを決定論的に判定する（自己申告禁止・原則 P1）。トリガー語彙: プロンプト AB, A/B 評価, ab-eval, 勝率マージン, プロンプト比較, 勝者判定, prompt ab eval
---

# prompt-ab-eval — プロンプト A/B の決定論勝者判定

> プロンプト（or モデル設定）A と B を **同一 eval** に通して得た 2 つのスコアセットを比較し、
> 「どちらが勝ちか」「差はマージンを超える決定的な差か」を機械的に判定する。
> LLM を呼ばず、入力から決定論的に算出する（同じ入力 → 同じ出力）。
> 原則 P1「自己申告禁止」の評価側実装。「A の方が良い気がする」で採用しない。

## 目的

- **勝敗の恣意判定を排除**する — A/B 各々を同じ採点系（citation-coverage / style-score / judge-vote 等の別 eval）で採点した数値スコア列を入力に取り、平均差 `diff = |mean_a − mean_b|` を計算する。
- **マージンゲート**で「誤差レベルの差」を勝ちと呼ばせない — `diff >= margin` を満たした時のみ `winner` を確定（`pass`）。満たさなければ `tie` として `fail`（採用不可）。
- ノイズ由来の僅差を「改善」と誤認するプロンプト沼を防ぐ。

## 入力

- A/B それぞれ、数値スコアを含む **JSON ファイル**（例: `[0.60, 0.62, 0.58]`、または各要素にスコアを持つ dict のリスト）。
  同一 eval を N 試行走らせた結果のスコア列を渡す想定。
- `--margin`（既定 0.05）= 決定的とみなす最小平均差。

## 実行手順

1. 比較する 2 プロンプト（A/B）を **同じ eval・同じデータセット**で走らせ、スコア列を `a.json` / `b.json` に保存する（採点は本ツールでなく既存の採点系や judge-vote を使う）。
2. 勝者・マージンを判定する:

   ```
   python3 scripts/eval-harness.py ab-eval --a a.json --b b.json --margin 0.1 [--json]
   ```

3. 出力の解釈:
   - `status=pass` + `winner=a|b` → 決定的な勝者。そのプロンプトを採用してよい。
   - `status=fail` + `winner=tie` → マージン内の僅差。**採用しない**（追加試行 or margin 再検討）。
   - `status=observe` → A/B に数値スコアが無い・ファイル読めず。判定不能（データ不足として扱う）。
   - `metrics`: `mean_a` / `mean_b` / `diff` / `n_a` / `n_b` を確認し、試行数 N が十分かも見る。
4. 採用判断・数値（勝者・diff・margin）を standup またはレビューに記録する。

## 決定論ゲート（--check 相当・自己申告禁止）

このスキルが「本物として動く」ことは以下 1 コマンドで機械確認する（hermetic・実源に触れない）:

```
python3 scripts/eval-harness.py ab-eval --self-test
```

- exit 0 = OK。`[PASS] ab-eval: good=True bad=True obs=True` を出力。
  - `good` = 明確な差で `pass` かつ `winner=b`
  - `bad`  = 同値なら `fail`（tie）
  - `obs`  = スコア源欠如で `observe`
- exit 1 = 実装退行。engine（`scripts/eval-harness.py`）側を修正する。

## 参照

- エンジン: `scripts/eval-harness.py`（サブコマンド `ab-eval` = `compute_ab_eval` / self-test = `st_ab_eval`）
- 全サブ一括自己テスト: `python3 scripts/eval-harness.py --self-test`
- 設計原則（observe/measured/gate・自己申告禁止）: `scripts/eval-harness.py` 冒頭コメント・原則 P1
- 関連 eval スキル: `judge-rubric-vote`（合議採点）/ `eval-regression-guard`（baseline 回帰）/ `eval-dataset-build`（golden dataset）
