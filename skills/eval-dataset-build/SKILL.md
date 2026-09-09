---
name: eval-dataset-build
description: golden eval データセットを JSON schema 検証で構築・検査する。records/errors を機械判定し pass/fail/observe を返す。トリガー語彙: eval データセット, golden dataset, データセット検証, dataset build, schema 検証, golden 構築, ゴールデンデータ, 評価データセット
---

# eval-dataset-build（golden eval データセットの schema 検証構築）

> `scripts/eval-harness.py` の `dataset-build` サブコマンド（engine フェーズ実装済）を skill 化したもの。
> golden eval データセット（prompt/expected の JSON 群）を schema に照らして検証し、欠損フィールド・型不一致・最小件数不足を機械的に検出する。自己申告禁止・決定論ゲートで pass/fail を確定する（原則 P1/P2）。

## 目的

- eval に使う golden dataset が schema に適合しているか（必須フィールド・型・最小件数）を **決定論的に** 検査する
- レコード数（records）と違反数（errors）を metrics として返し、緑（pass）/赤（fail）/観測（observe）を機械判定する
- 「十分そろっている気がする」で通さず、schema 検証を floor ゲートにする

## トリガー語彙

eval データセット / golden dataset / データセット検証 / dataset build / schema 検証 / eval-harness / ゴールデンデータ / 評価データセット

## 実行手順

1. データセット（JSON 配列 or オブジェクト）と schema（JSON）を用意する
   - dataset 例: `[{"prompt": "...", "expected": "..."}, ...]`
   - schema 例（flat 形式）: `{"required": ["prompt","expected"], "types": {"prompt":"string","expected":"string"}, "min_items": 2}`
   - schema は JSON Schema 風（`type`/`properties`/`items`/`required`/`minItems`）と flat（`required`/`types`/`min_items`）の両形式に対応
2. 検証を実走する:
   ```
   python3 scripts/eval-harness.py dataset-build --dataset <path> --schema <path>
   ```
3. 出力の `status` / `metrics.records` / `metrics.errors` / `findings` を確認する
   - `pass`: 違反 0 件（schema 全適合）
   - `fail`: 1 件以上の違反（`findings` に path + error）→ dataset を修正して再走
   - `observe`: dataset か schema が存在しない（不明扱い・0 件と解釈しない）

## 決定論ゲート（--check 相当・自己判定禁止）

skill・サブコマンドの健全性は hermetic self-test で確定する（実源に触れない fixture・exit code で判定）:

```
python3 scripts/eval-harness.py dataset-build --self-test
```

- exit 0 = ゲート緑（good=pass / bad=fail / missing=observe の 3 判定が全て正）
- exit 非0 = ゲート赤（サブコマンド破損 → 実データ検証に使わない）

実データセット検証時の合否は、実走出力の `status == "pass"`（かつ `metrics.errors == 0`）を機械判定する。空出力・例外は「不明」として扱い、pass と解釈しない。

## 参照

- 実装: `scripts/eval-harness.py`（`compute_dataset_build` / `validate_schema` / self-test `st_dataset_build`）
- 全サブ一括 self-test: `python3 scripts/eval-harness.py --self-test`（全11サブ pass で exit0）
- 決定論ゲート方針: `docs/incidents.md`（「ゴール到達型ループの停止は決定論ゲートで判定」）・原則 P1/P2
