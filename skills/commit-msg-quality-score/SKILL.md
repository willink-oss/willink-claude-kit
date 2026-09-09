---
name: commit-msg-quality-score
description: git 範囲の commit メッセージを決定論的に採点し、空虚コミット（「更新」「wip」等・prefix 正しくても中身なし）を機械的に検出する。トリガー語彙: commit 品質, コミットメッセージ採点, 空虚コミット検出, commit-score, なぜ欠落採点, commit message quality
---

# コミットメッセージ品質スコア（B05）

> `docs/incidents.md` の「空虚なコミットメッセージ禁止 — prefix が正しくても『〜を更新』で終わらせない。『なぜ』を書く」を **自己申告でなく機械採点** で enforce する。
> 原則 P1（自己申告禁止）の評価側実装。LLM を呼ばず、入力から決定論的にスコアを算出する（同じ入力 → 同じ出力）。engine は `scripts/eval-harness.py` の `commit-score` サブコマンド。

## 目的

- commit msg を 3 軸で採点し平均スコア（0.0〜1.0）を出す。空虚コミットを findings として列挙する。
  - **prefix_ok**（0.34）: `feat:` `fix:` `docs:` `ops:` 等の許容 prefix か（対象リポジトリ 固有: `pm/harness/research/config/comply/grow/launch/design`）
  - **not_empty**（0.33）: 説明が 6 文字以上、かつ `update/更新/wip/修正/作業` 等の placeholder 単独でない
  - **has_why**（0.33）: 説明が 25 文字以上、または「なぜ」マーカ（`ため/理由/なぜ/回避/防止/→/because/so that` 等）を含む
- 単体スコア < 0.67 の commit を weak（空虚候補）として findings に出す。

## 実行手順

```bash
# 計測のみ（直近 7 日・閾値なし → status=measured / exit 0）
python3 scripts/eval-harness.py commit-score

# 範囲・閾値を指定して gate（未達で status=fail / exit 1）
python3 scripts/eval-harness.py commit-score --since "30 days ago" --min 0.6 --json

# 別リポジトリを対象にする
python3 scripts/eval-harness.py commit-score --root /path/to/repo --since "7 days ago" --min 0.6
```

- `--since`: git log の範囲（デフォルト `7 days ago`）
- `--min`: gate 閾値。**省略時は計測のみ（exit 0）**、指定時のみ未達で exit 1
- `--root`: 対象リポジトリ（省略時はカレント）
- `--json`: 機械可読出力（`status/score/threshold/metrics/findings`）

## 決定論ゲート（--check 相当・自己判定禁止）

```bash
python3 scripts/eval-harness.py commit-score --self-test
```

- temp git repo を作って実源に触れず hermetic 検証（good=pass / bad=fail / None=observe / git 経路）。全て満たせば exit 0。
- ハードコード成功禁止・「十分だろう」で完了としない。停止判定はこのゲートで機械的に確認する（原則 P1/P2）。

## 設計上の注意

- **対象無は observe**: git 不在・範囲に commit 無しは status=observe（exit 0）。データ不在を「合格」とも「不合格」とも言わない。
- **Merge コミット・`[routine]` prefix** は許容 prefix 外のため weak に上がりうる（prefix_ok=false）。gate 閾値運用時はこの母集団特性を踏まえる。
- 依存は python3 標準ライブラリのみ（BSD/macOS でそのまま動く・`grep -P` 不使用）。

## 参照

- engine: `scripts/eval-harness.py`（`compute_commit_score` / `score_commit_msg` / `st_commit_score`）
- ルール本体: `docs/incidents.md`「コミット品質」節
- 姉妹 skill: `media-quality-score-gate`（記事）/ `standup`（standup-novelty）— 同 harness の別サブコマンド
