---
name: knowledge-dedup-scan
description: <KNOWLEDGE_DIR>/*.md の title を正規化して近似重複クラスタを検出し、統合候補を決定論的に提示する。トリガー語彙: ナレッジ重複, knowledge dedup, 重複ナレッジ, ナレッジ統合候補, 近似重複クラスタ, knowledge dedup scan, ナレッジ棚卸し
---

# knowledge-dedup-scan（ナレッジ近似重複クラスタの検出と統合候補提示）

> `<KNOWLEDGE_DIR>/` は 対象リポジトリ の学習資産。同じ主題のレポートを別日付・別語尾で複数回書くと、
> 「title は実質同一だがファイルは別」という**近似重複**が溜まり、想起が非決定的になり索引が肥大する。
> title を日付・括弧・区切りを剥がして正規化し、同一キーに 2 本以上ぶら下がるクラスタを機械検出する。
> 各クラスタには先頭段落の語彙 Jaccard（`para_similarity`）を付し、統合判断の材料にする。
> 原則 P1「自己申告禁止」・決定論ゲート（`--self-test` exit0）で floor を保証する。

## 目的

- `<KNOWLEDGE_DIR>/*.md` の各ファイルの先頭見出し（title）を `_norm_title` で正規化（`（）`括弧内・`YYYY-MM-DD`日付・`YYYY`年号・区切り記号を除去し lower 化）する
- 正規化キーが一致するファイルが 2 本以上あるクラスタを **統合候補** として列挙する
- 各クラスタに先頭段落の distinctive term Jaccard（`para_similarity`）を付し、本文まで近いかを示す
- knowledge dir 欠如 / md 無しは「対象無・観測継続」として exit0（欠如≠違反・空≠ゼロ件）

## 統合は「1 主題 = 1 canonical」に寄せる

- クラスタが出たら、**最新かつ包括的な 1 本を canonical** に残し、旧版の固有情報だけをそこへ吸い上げる。
- 主題が実は別（title は似るが観点が違う）なら、**title を固有化して類似度を下げる**（曖昧な包括 title を増やして重複を隠さない — Counter-Convergence: 要求外の追記禁止）。
- 月次更新レポート系（`-may-update` / `-june-2026-update` 等）は追記統合か、旧月版のアーカイブ移動を検討する。
- 統合で消すファイルがあれば `<TARGET>/assets/knowledge-base.md` 索引を同時に直し、`python3 scripts/regenerate-knowledge-index.py --check` で broken links: 0 を確認する。

## 実行手順

1. `python3 scripts/govern.py knowledge-dedup` を実走し、クラスタ数と各クラスタの members / count / para_similarity を確認する
   - 入力: `<root>/<KNOWLEDGE_DIR>/*.md`（`--knowledge-dir DIR` で差替可・`--root DIR` で repo ルート差替可）
   - `--json` で機械可読出力（`findings[].key` / `findings[].members` / `findings[].count` / `findings[].para_similarity` / `counts.files` / `counts.clusters`）
   - 出力例: `key=中小企業ai導入成功事例roi members=[...3本...] count=3 para_similarity=0.0`
2. クラスタごとに統合判断する:
   - **実質同一** → 最新 1 本を canonical に残し旧版の固有情報を吸収、旧ファイルを削除し索引を更新
   - **別主題** → title を固有化して正規化キーを分離（次回スキャンでクラスタから外れる）
   - **月次更新系** → 追記統合 or 旧月版アーカイブ
3. `para_similarity` が高い（本文も近い）クラスタから優先処理する。低くても title 同一なら重複の疑いがあるため目視確認する
4. 統合後、`python3 scripts/govern.py knowledge-dedup` を再実走してクラスタ減を確認し、索引再生成 check を通してからコミットする
5. 月次 `/harness-review` の C 項（ルール整合性）や四半期棚卸しで本スキャンの exit と clusters 件数を記録する

## 決定論 --check（コマンドと exit の意味）

このサブに専用 `--check` フラグは無い。決定論ゲートは `--self-test`、運用時の違反判定は live 実走の exit で行う。

```
python3 scripts/govern.py knowledge-dedup --self-test
```

- **exit 0** = self-test PASS: hermetic temp fixture で検証する。title が別日付・別括弧で実質同一の `a.md`/`b.md` を仕込み**同一クラスタとして検出**され、固有 title の `c.md` はクラスタに入らないこと、加えて空 dir が「対象無・観測継続」exit0 になる耐性を assert する。fixture は tempfile に作り実行後に削除する（リポジトリに残さない）。
- **exit 非0** = FAIL: title 正規化・クラスタリング・Jaccard 計算・対象欠如耐性のいずれかが壊れている。

運用時の違反ゲート（live 実走の exit をそのまま使う）:

```
python3 scripts/govern.py knowledge-dedup
```

- **exit 0** = クラスタ 0（統合候補なし）、または対象欠如（`対象無・観測継続`）
- **exit 1** = 近似重複クラスタ ≥ 1（members を stdout に印字。統合候補あり）

全サブ一括の floor 確認は `python3 scripts/govern.py --self-test`（全9サブ pass で exit0）。

## 参照

- エンジン: `scripts/govern.py`（サブコマンド `knowledge-dedup` / `compute_knowledge_dedup` / self-test `st_knowledge_dedup`）
- 対象: `<KNOWLEDGE_DIR>/*.md`（学習資産の実体）
- 索引: `<TARGET>/assets/knowledge-base.md` ／ 再生成 `scripts/regenerate-knowledge-index.py`（統合後の broken links: 0 確認）
- 姉妹スキャン: `skills/knowledge-index-guard/`（索引整合）・`skills/rule-dedup-scan/`（ルール重複）・`skills/memory-distill/`（メモリ索引蒸留）
- 月次実走: `harness-review`（本バンドル外）（C 項・四半期棚卸し）
- 設計原則: 原則 P1（自己申告禁止・決定論ゲート）／ `docs/incidents.md`（Counter-Convergence: 要求外追記禁止・ナレッジ重複エントリ禁止）
