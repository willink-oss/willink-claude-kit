---
name: task-success-rate-track
description: goal-loop の帰結ログ群（GOAL MET / CAP REACHED）を集計し、タスク成功率（--check 到達率）を決定論的に採点する。トリガー語彙: タスク成功率, 成功率集計, success rate, goal-loop 到達率, ループ帰結集計, CAP 率, task success rate
---

# タスク成功率トラッキング（B10）

> ゴール到達型ループ（`/goal-loop`・`scripts/goal-loop.sh`）が **決定論ゲート `--check` に到達して停止したか（GOAL MET）**、それとも **試行上限で打ち切ったか（CAP REACHED）** を帰結ログ群から集計し、タスク成功率を機械採点する。
> `docs/incidents.md`「ゴール到達型ループの停止は決定論ゲートで判定する（自己判定禁止）」の観測側実装。原則 P1（自己申告禁止）/ 原則 P2（試行上限）の効果を実測で可視化する。
> LLM を呼ばず、入力から決定論的にスコアを算出する（同じ入力 → 同じ出力）。engine は `scripts/eval-harness.py` の `success-rate` サブコマンド（実装済）。

## 目的

- goal-loop の帰結が集まったディレクトリを走査し、成功率 `rate = success / (success + cap)` を出す
  - **success**: ログ本文に `GOAL MET` トークン、または `.json` レコードの `outcome` が `met/success/pass/passed/done/ok`
  - **cap**: ログ本文に `CAP REACHED` トークン、または `.json` レコードの `outcome` が `cap/fail/failed/timeout/aborted/capped`
- `metrics` に `success / cap / total` を返し、緑（pass）/赤（fail）/観測（observe）を機械判定する
- 「最近ループはだいたい通っている気がする」で報告せず、到達率を floor ゲートにする

## トリガー語彙

タスク成功率 / 成功率集計 / success rate / goal-loop 到達率 / ループ帰結集計 / CAP 率 / task success rate

## 実行手順

1. goal-loop の帰結ログ（`.log` / `.json` 等）が集まったディレクトリを対象にする（`rglob("*")` で再帰走査するため入れ子でも可）
2. 集計を実走する:
   ```
   # 計測のみ（閾値なし → status=measured/pass 判定なし・exit 0）
   python3 scripts/eval-harness.py success-rate --dir <帰結ログdir>

   # 閾値を指定して gate（未達で status=fail / exit 1）
   python3 scripts/eval-harness.py success-rate --dir <帰結ログdir> --min 0.9 --json
   ```
3. 出力の `status` / `score`（成功率）/ `metrics.success` / `metrics.cap` / `metrics.total` を確認する
   - `pass`: `--min` 未指定、または `rate >= --min`
   - `fail`: `rate < --min`（成功率が floor 未達 → ループ prompt / ゲート設計を見直す）
   - `observe`: dir が存在しない、または帰結 0 件（不明扱い・「成功率 0」とも「100」とも解釈しない）

- `--dir`: goal-loop 帰結ログのディレクトリ
- `--min`: gate 閾値（成功率の下限）。**省略時は計測のみ（exit 0）**、指定時のみ未達で exit 1
- `--json`: 機械可読出力（`status/score/threshold/metrics`）

## 決定論ゲート（--check 相当・自己判定禁止）

skill・サブコマンドの健全性は hermetic self-test で確定する（実源に触れない fixture・exit code で判定）:

```
python3 scripts/eval-harness.py success-rate --self-test
```

- exit 0 = ゲート緑（good dir=pass / bad dir=fail / 空 dir=observe / 不在 dir=observe の 4 判定が全て正）
- exit 非0 = ゲート赤（サブコマンド破損 → 実データ集計に使わない）

実データ集計時の合否は、実走出力の `status == "pass"` を機械判定する。空出力・例外は「不明」として扱い、pass と解釈しない（`docs/incidents.md`「空出力 ≠ ゼロ件」）。

## 設計上の注意

- **帰結無は observe**: dir 不在・帰結 0 件は status=observe（exit 0）。データ不在を「合格」とも「不合格」とも言わない。ループを一度も回していない段階で成功率を報告しない。
- **CAP は失敗ではなく到達不能の記録**: `CAP REACHED` は試行上限で安全に打ち切った健全な停止（原則 P2）。cap 率が高い＝ゲート未達が続いているサイン → ループ prompt かゲート閾値の見直し材料であって、ループ機構の故障ではない。
- **トークンは本文カウント**: 1 ファイル内に複数の `GOAL MET` / `CAP REACHED` があれば各出現を数える。1 ループ = 1 ファイル = 1 トークンになるようログ設計するのが望ましい（集約ログを 1 ファイルに混在させると母数がぶれる）。
- 依存は python3 標準ライブラリのみ（BSD/macOS でそのまま動く・`grep -P` 不使用）。

## 参照

- engine: `scripts/eval-harness.py`（`compute_success_rate` / `_scan_outcomes` / self-test `st_success_rate`）・トークン定義 `SUCCESS_TOKENS` / `CAP_TOKENS` / `JSON_SUCCESS` / `JSON_CAP`
- 全サブ一括 self-test: `python3 scripts/eval-harness.py --self-test`（全11サブ pass で exit0）
- 帰結の生成側: `scripts/goal-loop.sh` / `/goal-loop`（原則 P2）・`goal-loop-template`（本バンドル外）
- 決定論ゲート方針: `docs/incidents.md`「ゴール到達型ループの停止は決定論ゲートで判定」・原則 P1/P2
- 姉妹 skill: `commit-msg-quality-score` / `eval-dataset-build` / `media-quality-score-gate` — 同 harness の別サブコマンド
