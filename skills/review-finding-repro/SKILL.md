---
name: review-finding-repro
description: 同一 diff を複数回レビューした時の finding セット群の Jaccard 一致率を決定論的に測り、レビューの再現性（安定性）を機械採点する enforcement primitive。トリガー語彙: レビュー再現性, finding 一致率, review repro, Jaccard 再現性, レビュー安定性, 指摘再現率, review reproducibility
---

# review-finding-repro（レビュー指摘の Jaccard 再現性ゲート）

> 同じ diff を同じ rubric で複数回レビューしても毎回違う指摘が出るなら、そのレビューは
> 信頼できない（stochastic すぎる）。だが「今回のレビューは前回と同じ観点を拾えたか」は
> 主観に流れやすく、自己申告では「安定している」と誤宣言しがち（原則 P1: 自己申告禁止）。
> 本 skill は同一対象への複数レビューが出した **finding セット群**を受け取り、全ペアの
> Jaccard 一致率の平均（mean_jaccard）を機械的に測って、閾値未満を exit code で落とす。
> Generator-Verifier 分離のレビュー段（`/review` 子セッション・judge-rubric-vote）の
> 出力が再現するかを floor で担保する部品。

## 目的

- 同一 diff への複数 finding セット（`[["f1","f2"], ["f1","f3"], ...]`）を受け取り、
  **全ペア Jaccard の平均（mean_jaccard）** を決定論採点する（大きいほど再現性が高い）。
- `mean_jaccard >= --min-jaccard`（既定 0.5）を PASS、未満を FAIL として機械的に落とす floor ゲート。
- H3（ブロッキング検証）レイヤの部品。goal-loop / レビュー生成物の `--check` に組み込める。
- finding セットが 2 未満（＝ペアを作れない）は「再現性測定不能・観測継続」として exit0（欠如≠違反）。

## 判定ロジック（`compute_review_repro`）

- `--sets` の JSON（list の list）を読む。各要素の inner list を文字列集合（finding id）に正規化。
- list でない / 有効な finding セットが 2 未満 → `observe`（測定不能・exit0）。
- 全ペア (i<j) の `jaccard(sets[i], sets[j]) = |A∩B| / |A∪B|` を算出し平均を取る。
- `mean_jaccard` を下限（mode=min）として `--min-jaccard` でゲート。
- metrics に `sets`（セット数）/ `pairs`（ペア数）/ `min_pair` / `max_pair` を含める。

## 実行手順

1. 採点: `python3 scripts/eval-harness.py review-repro --sets <finding-sets.json> --min-jaccard 0.5`
   - `mean_jaccard >= min-jaccard` → exit 0（PASS・内訳を stdout: sets / pairs / min_pair / max_pair）
   - `mean_jaccard <  min-jaccard` → exit 1（FAIL・レビューが再現していない）
   - sets 読取不能 / list でない / セット 2 未満 → exit 0（observe・測定不能）
   - `--json` で機械可読出力
2. FAIL なら rubric を明確化・温度を下げる・judge-rubric-vote で合議するなどして再現性を上げ、再採点する。
   - finding id の粒度（表記ゆれ）が原因なら、正規化した id で集合を作り直してから測る。
3. goal-loop で回す場合:
   `scripts/goal-loop.sh --check "python3 scripts/eval-harness.py review-repro --sets <sets.json> --min-jaccard 0.5"`

## 決定論 --check（自己テスト）

```
python3 scripts/eval-harness.py review-repro --self-test
```

- **exit 0** = self-test PASS: hermetic temp fixture で **good（ほぼ同一の finding セット群）が pass・bad（毎回バラバラ）が fail・単一セットが observe** を assert（スコアラが機能）。fixture は tempfile に生成し実行後に削除（リポジトリに残さない）。
- **exit 非0** = FAIL: JSON 読取・集合正規化・ペア Jaccard 平均・欠如耐性（2 未満→observe）のいずれかが壊れている。

全11サブ一括の floor 確認は `python3 scripts/eval-harness.py --self-test`（全 pass で exit0）。

## 参照

- エンジン: `scripts/eval-harness.py`（サブコマンド `review-repro` / `compute_review_repro` / helper `jaccard` / self-test `st_review_repro`）
- 停止プリミティブ: `scripts/goal-loop.sh`（`--check` に本スコアラを渡す）
- レビュー段: `docs/practices.md`（`/review` 読取専用 Evaluator・Generator-Verifier 分離）
- 姉妹スコアラ: `judge-rubric-vote`（本バンドル外）（複数 vote の合議）・`standup-novelty-score`（本バンドル外）・`skills/eval-regression-guard/`
- 設計原則: 原則 P1（自己申告禁止・決定論ゲート）
