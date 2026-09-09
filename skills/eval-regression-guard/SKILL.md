---
name: eval-regression-guard
description: 現行の評価スコアを baseline と比較し、許容誤差を超える回帰を検知してブロックする。metrics ごとに delta を機械判定し pass/fail/observe を返す。トリガー語彙: 回帰検知, regression guard, スコア回帰, baseline 比較, 回帰ガード, スコア低下検知, 性能後退, リグレッション
---

# eval-regression-guard（現行 vs baseline スコアの回帰検知ゲート）

> `scripts/eval-harness.py` の `regression-guard` サブコマンド（engine フェーズ実装済）を skill 化したもの。
> 現行スコア（記事カバレッジ・style・成功率などの JSON 指標群）を baseline に照らし、`tolerance` を超えて低下した指標を回帰として検出しブロックする。自己申告禁止・決定論ゲートで pass/fail を確定する（原則 P1/P2）。

## 目的

- eval 指標が前回 baseline から悪化していないかを **決定論的に** 検査し、回帰があれば赤（fail）で止める
- baseline の各指標に対し `current < baseline - tolerance` を回帰と判定（current に欠損した指標も回帰扱い）
- 「体感で悪くなっていない」で通さず、baseline 差分を floor ゲートにする（性能後退の見逃し防止）

## トリガー語彙

回帰検知 / regression guard / スコア回帰 / baseline 比較 / 回帰ガード / eval-harness / 性能後退 / リグレッション

## 実行手順

1. baseline と現行のスコア JSON を用意する（キー = 指標名、値 = 数値スコア）
   - baseline 例: `{"coverage": 0.9, "style": 0.8}`
   - current 例: `{"coverage": 0.92, "style": 0.8}`
   - list 形式 `[{"name": "coverage", "score": 0.9}, ...]` や `{"coverage": {"score": 0.9}}` のネストも解釈する
2. 回帰検知を実走する:
   ```
   python3 scripts/eval-harness.py regression-guard --current <path> --baseline <path> [--tolerance 0.0]
   ```
   - `--tolerance`（省略時 0.0）: この許容幅までの低下は回帰としない（ノイズ吸収）
3. 出力の `status` / `metrics.metrics_checked` / `metrics.regressed` / `findings` を確認する
   - `pass`: 回帰 0 件（全指標が baseline − tolerance 以上）
   - `fail`: 1 件以上の回帰（`findings` に metric / baseline / current / delta、または `missing in current`）→ 原因を修正して再走
   - `observe`: current か baseline が存在しない/壊れている、または baseline に数値指標が無い（不明扱い・pass と解釈しない）

## 決定論ゲート（--check 相当・自己判定禁止）

skill・サブコマンドの健全性は hermetic self-test で確定する（実源に触れない fixture・exit code で判定）:

```
python3 scripts/eval-harness.py regression-guard --self-test
```

- exit 0 = ゲート緑（good=pass / bad=fail / missing=observe の 3 判定が全て正）
- exit 非0 = ゲート赤（サブコマンド破損 → 実データ回帰検知に使わない）

実データ回帰検知時の合否は、実走出力の `status == "pass"`（かつ `metrics.regressed == 0`）を機械判定する。空出力・例外は「不明」として扱い、pass と解釈しない。

## 参照

- 実装: `scripts/eval-harness.py`（`compute_regression_guard` / `_to_score_map` / self-test `st_regression_guard`）
- 全サブ一括 self-test: `python3 scripts/eval-harness.py --self-test`（全11サブ pass で exit0）
- 決定論ゲート方針: `docs/incidents.md`（「ゴール到達型ループの停止は決定論ゲートで判定」）・原則 P1/P2
