---
name: knowledge-citation-coverage
description: 記事・ナレッジの主張に対する出典 URL 充足率を決定論採点し、閾値未満を不合格にするゲート。トリガー語彙: 出典充足率, citation coverage, 引用カバレッジ, 出典チェック, ソース裏取り, 記事の裏取り, 主張の出典, 公開前チェック
---

# knowledge-citation-coverage

> 公開文（記事・ナレッジ・レポート）の**数値・断定を含む主張が出典で裏付けられているか**を機械採点する B 系評価ゲート。
> 原則 P1「自己申告禁止」の実装。「出典は付けたつもり」で公開せず、決定論スコアが閾値以上であることを実測してから合格とする。
> エンジンは `scripts/eval-harness.py` のサブコマンド `citation-coverage`（B02）。

## 目的

- 記事の**主張（claim）**を抽出し、各主張が属するセクションに**出典（URL / markdown リンク / 出典・参考・references マーカー）**があるかを判定する。
- 充足率 `supported / total` を算出し、`--min` 閾値と比較して **pass / fail** を決める。
- 責任者 報告・公開・queue 投入の前段ゲートとして使い、裏取りの薄い草稿を機械的に差し戻す。

### 何を「主張」と見なすか（誤検出しないための前提）

- 見出し行・出典マーカー行・4 文字未満の断片は主張に数えない。
- 数字を含む行、または強い主張キーワード（`によると` `によれば` `調査` `報告` `実績` `統計` `データ`）を含む行を主張とする。
- セクション内に URL / リンク / 出典マーカーがあれば、そのセクションの主張は「裏付けあり」と数える（行内 URL でも可）。

## 実行手順

1. 対象ファイルを 1 つ指定して採点する（閾値は用途に応じて。公開記事は `0.8` 目安）。

   ```
   python3 scripts/eval-harness.py citation-coverage --file <path.md> --min 0.8
   ```

   JSON で機械可読に受け取る場合:

   ```
   python3 scripts/eval-harness.py citation-coverage --file <path.md> --min 0.8 --json
   ```

2. 出力の `status` を確認する。
   - `pass`（exit 0）: 充足率 ≥ 閾値。公開・次段へ進んでよい。
   - `fail`（exit 1）: 充足率 < 閾値。`supported` / `claims` を見て、裏付けの無い主張に出典 URL を追記してから再採点する。
   - `observe`（exit 0）: 主張 0 件 or ファイル無。ゲート対象外（裏取り不要な文章か、パス誤り）。`--min` を渡しても observe は落とさない。

3. `fail` の場合はスコアを口頭申告で埋めず、**出典を実際に追記して再実行し pass を実測**してから完了とする。

## 決定論ゲート（--check 相当）

このスキル自体の健全性（エンジンが壊れていないこと）は self-test で機械確認する。self-test は good=pass / bad=fail / none=observe の 3 ケースを hermetic に検証する。

```
python3 scripts/eval-harness.py citation-coverage --self-test
```

- exit 0 = 3 ケース全通過（スキル利用可）。exit 1 = エンジン破損（利用前に修正）。
- 実タスクのゲート結果（記事が合格か）は上記「実行手順」の `--file ... --min ...` の exit code（fail=1）で判定する。自己判定・目視で pass を宣言しない（原則 P1/P2）。

## 参照

- エンジン本体・全 11 サブコマンド: `scripts/eval-harness.py`
- 姉妹ゲート: `style-score`（文体適合）/ `halluc-rate`（unsupported 主張率）/ `standup-novelty`（前版重複）
- 承認レベル・自己申告禁止の背景: `docs/principles.md`（原則 P3）/ 原則 P1・P2
- 公開文の文体基準: `<KNOWLEDGE_DIR>/` の style-guide
