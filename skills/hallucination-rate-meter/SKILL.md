---
name: hallucination-rate-meter
description: 文書の主張を出典照合し、裏付けの無い（unsupported）主張の割合を決定論採点して閾値超を不合格にするゲート。トリガー語彙: ハルシネーション率, hallucination rate, unsupported 率, 幻覚率計測, 裏取り率, 未裏付け主張, 主張の出典照合, 公開前ハルシネーションチェック
---

# hallucination-rate-meter

> 公開文・レポート・ナレッジの**数値/断定を含む主張のうち、近傍に出典 URL が無いもの（unsupported）の割合**を機械採点する B 系評価ゲート。
> 原則 P1「自己申告禁止」の実装。「ちゃんと裏取りしたつもり」で出さず、決定論スコア（unsupported 率）が閾値以下であることを実測してから合格とする。
> エンジンは `scripts/eval-harness.py` のサブコマンド `halluc-rate`（B09）。

## 目的

- 文書中の**主張（claim）**を抽出し、各主張の**近傍（±1 行の窓）**に出典（URL / markdown リンク）が無いものを **unsupported** として数える。
- unsupported 率 `unsupported / claims` を算出し、`--max-rate` 閾値と比較して **pass / fail** を決める（率は低いほど良い＝上限ゲート）。
- 責任者 報告・公開・queue 投入の前段ゲートとして使い、裏付けの薄い草稿を機械的に差し戻す。
- 姉妹スキル `knowledge-citation-coverage`（B02・充足率＝支持された主張の割合を下限ゲート）と表裏。こちらは**未裏付けの割合を上限で締める**。

### 何を「主張」と見なすか（誤検出しないための前提）

- 見出し行・出典マーカー行・4 文字未満の断片は主張に数えない。
- 数字を含む行、または強い主張キーワード（`によると` `によれば` `調査` `報告` `実績` `統計` `データ`）を含む行を主張とする。
- 主張行の前後 1 行以内（当該行含む窓）に URL / markdown リンクがあれば「裏付けあり」、無ければ unsupported と数える（行内 URL でも可）。

## 実行手順

1. 対象ファイルを 1 つ指定して採点する（閾値は用途に応じて。公開記事は unsupported 率上限 `0.2` 目安）。

   ```
   python3 scripts/eval-harness.py halluc-rate --file <path.md> --max-rate 0.2
   ```

   JSON で機械可読に受け取る場合:

   ```
   python3 scripts/eval-harness.py halluc-rate --file <path.md> --max-rate 0.2 --json
   ```

   `--max-rate` を省略すると閾値なしで率のみ計測（status=`measured`）。

2. 出力の `status` を確認する。
   - `pass`（exit 0）: unsupported 率 ≤ 閾値。公開・次段へ進んでよい。
   - `fail`（exit 1）: unsupported 率 > 閾値。`findings` の各 `unsupported_claim` に出典 URL を追記してから再採点する。
   - `observe`（exit 0）: 主張 0 件 or ファイル無。ゲート対象外（裏取り不要な文章か、パス誤り）。`--max-rate` を渡しても observe は落とさない。
   - `measured`（exit 0）: 閾値未指定。率だけ知りたいとき。

3. `fail` の場合はスコアを口頭申告で埋めず、**出典を実際に追記して再実行し pass を実測**してから完了とする（原則 P1/P2）。

## 決定論ゲート（--check 相当）

このスキル自体の健全性（エンジンが壊れていないこと）は self-test で機械確認する。self-test は good=pass / bad=fail / none=observe の 3 ケースを hermetic に検証する。

```
python3 scripts/eval-harness.py halluc-rate --self-test
```

- exit 0 = 3 ケース全通過（スキル利用可）。exit 1 = エンジン破損（利用前に修正）。
- 実タスクのゲート結果（文書が合格か）は上記「実行手順」の `--file ... --max-rate ...` の exit code（fail=1）で判定する。自己判定・目視で pass を宣言しない。

## 参照

- エンジン本体・全サブコマンド: `scripts/eval-harness.py`
- 姉妹ゲート（出典充足率・下限）: `skills/knowledge-citation-coverage/SKILL.md`（B02）
- ハルシネーション教訓ログ: `hallucination-lesson-log`（本バンドル外）
- 自己申告禁止・決定論ゲート原則: 原則 P1/P2・`docs/incidents.md`
