---
name: mistake-rule-distill
description: mistake-log-archive.md のミス経緯のうち common-mistakes.md にルール化（蒸留）されていないものを決定論的に抽出し、現役ルールへの昇格候補を提示する。トリガー語彙: ミス蒸留, mistake distill, ルール化候補, 未蒸留ミス, ミスログ蒸留, common-mistakes 追記候補, rule distill
---

# mistake-rule-distill（未蒸留ミスのルール化候補抽出）

> ミスの経緯は `<KNOWLEDGE_DIR>/mistake-log-archive.md`（常駐外）に、そこから抽出した**現役の行動ルール 1 行**は `docs/incidents.md`（常駐）に置く二層構造である（`docs/practices.md`）。
> 経緯を書いたのにルール化を忘れると、同じミスが再発しても常駐側に予防則が無い。
> 本 skill は archive のミスエントリと現役ルールを機械照合し、**ルール化されていない（未蒸留の）ミス**を列挙して昇格を促す（原則 P1 自己申告禁止・決定論ゲート）。

## 目的

- `<KNOWLEDGE_DIR>/mistake-log-archive.md` の日付ミスエントリ（`### YYYY-MM-DD: title`）を全件抽出する
- 各ミスが `docs/incidents.md` に蒸留済みかを判定する
  - 判定: 当該**日付が rules に出現** **または** ミス本文と rules の distinctive term が **2 語以上重複**（`--min-overlap`・既定 2）すれば「蒸留済」
- 未蒸留のミスを findings として列挙し、`common-mistakes.md` へのルール 1 行追記候補とする
- archive / rules 欠如・ミスエントリ 0 件は「対象無・観測継続」として exit0（欠如≠違反・空≠ゼロ件）

## 未蒸留ミスは「ルール 1 行」に寄せる（塗り足さない）

- 未蒸留が出たら、対応する**行動ルールを 1 行だけ** `docs/incidents.md` の該当セクションに追記する（詳細経緯は archive 側に留める）。
- 常駐コンテキストを肥大させない: 長い説明・重複エントリを足さない（Counter-Convergence: 要求外の追記禁止 / ナレッジ重複エントリ禁止）。
- 既に趣旨が同じ現役ルールがある場合はルール文言に日付・キーワードを織り込むだけで済む場合がある（照合は term 重複で行われるため、蒸留済と判定されれば findings から外れる）。

## 実行手順

1. `python3 scripts/govern.py distill` を実走し、未蒸留ミスの件数と該当エントリを確認する
   - 入力: `<KNOWLEDGE_DIR>/mistake-log-archive.md` ＋ `docs/incidents.md`（`--root DIR` で repo ルート差替可）
   - `--json` で機械可読出力（`findings[]` = `date` / `title` / `matched_terms` / `reason`・`counts` = `mistakes` / `undistilled`）
   - 出力例: `- date=2026-09-09 title=… matched_terms=[] reason=common-mistakes に対応ルール無し`
2. findings の各ミスについて、`docs/incidents.md` の該当カテゴリに**行動ルール 1 行**を追記する（経緯は archive 側に残す）
3. 追記後に再実走し `undistilled` が減った（該当ミスが findings から外れた）ことを確認してからコミットする
4. 月次 `/harness-review` の A 項（ミスログ振り返り・`common-mistakes.md` に未対応のミスログがないか）で本サブの exit と未蒸留件数を記録する

## 決定論 --check（コマンドと exit の意味）

```
python3 scripts/govern.py distill --self-test
```

- **exit 0** = self-test PASS: hermetic temp fixture（archive に「蒸留済ミス（amplify・rules と term 重複）」と「未蒸留ミス（xyzzy/frobnicate・重複無し）」の 2 件を置く）で、
  未蒸留ミスのみが findings に出て蒸留済ミスは出ないこと、および archive 欠如で「対象無・観測継続」exit0 になる耐性を assert する。fixture は tempfile に作り実行後に削除する（リポジトリに残さない）。
- **exit 非0** = FAIL: 日付エントリ抽出（`_parse_dated_entries`）・term 重複照合（`_significant_terms`）・対象欠如耐性のいずれかが壊れている（stderr に失敗内容を印字）。

運用時の状態ゲート（real run の exit 意味）:

```
python3 scripts/govern.py distill
```

- **exit 0** = 未蒸留 0（全ミスがルール化済）または archive/rules 欠如（観測継続）
- **exit 1** = 未蒸留 > 0（ルール化候補あり・findings を stdout に印字）

## 参照

- 実装: `scripts/govern.py` サブコマンド `distill`（`compute_distill` / `_parse_dated_entries` / `_significant_terms` / 自己テスト `st_distill`）
- 検査対象: `<KNOWLEDGE_DIR>/mistake-log-archive.md`（ミス経緯・常駐外）＋ `docs/incidents.md`（現役ルール・常駐）
- 二層構造の運用: `docs/practices.md`（ミス発生時＝経緯は archive・ルール 1 行は common-mistakes）
- 姉妹 skill: mistake-pattern-classifier（ミスの類型分類）/ rule-promotion-extract（rules→フック昇格）/ rule-promote-audit / harness-review（月次 A 項ミスログ振り返り）
- 設計原則: 原則 P1（自己申告禁止・決定論ゲート・閾値は緩めない）/ ハーネス再設計 H1（常駐コンテキスト最小化・新規ミスはルール 1 行のみ追記）
