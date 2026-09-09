---
name: rule-promote-audit
description: docs/ の自然言語ルールを棚卸しし、common-mistakes.md の advisory 記述から H3(blocking 検証)への昇格候補を機械的に抽出する。トリガー語彙: 自然言語ルール棚卸し, rule-promote audit, ルール行数集計, ルール昇格監査, H3 検証昇格候補, rule promote
---

# rule-promote-audit（自然言語ルール棚卸し + H3 昇格候補抽出）

> `harness-lint.py rule-promote` サブコマンドの skill ラッパ。月次 `/harness-review` の一次資料になる観測 skill。
> 常駐ルール（`docs/*.md`）は自然言語で書かれ、放置すると肥大・形骸化する。
> どのファイルに何行の自然言語ルールがあるかを機械集計し、`common-mistakes.md` の
> advisory 記述のうち**再発語を含む**ものを H3（BLOCK フック等の決定論検証）への
> 昇格候補として列挙する（原則 P1 自己申告禁止・決定論ゲート）。

## 目的

- `docs/*.md` の箇条書き（自然言語ルール行）をファイル別に集計し常駐総量を可視化
- `common-mistakes.md` の advisory bullet のうち再発語（再発 / 繰り返 / 習慣 / 毎回 / 何度も 等）を
  含むものを **H3 昇格候補** として抽出する（習慣化した逸脱は決定論検証に格上げすべき証拠）
- rules dir 欠如は「対象無・観測継続」として exit0（欠如は失敗ではない・空≠ゼロ件）

## 姉妹 skill との棲み分け

- **本 skill（rule-promote-audit）**: 入力 = `docs/*.md`（静的ルール文書）。ルール行の棚卸し + common-mistakes.md の advisory 記述からの昇格候補。
- **rule-promotion-extract**: 入力 = `.claude/logs/advisory-fires.jsonl`（advisory フックの発火ログ）。実発火の再発回数集計。
- 両者は入力源が異なる（文書 vs ランタイムログ）。両方を突合すると「文書に書いた advisory」と「実際に何度も発火した advisory」の両面から昇格候補を検証できる。

## 実行手順

1. `python3 scripts/harness-lint.py rule-promote` を実行し人間可読サマリを確認
   - 出力: rule 行総数 / ファイル別行数 / H3 昇格候補（advisory×再発）
   - `--json` で機械可読出力（`total_rule_lines` / `per_file[]` / `items[]` / `violations`）
   - `--root DIR` / `--rules-dir DIR` で対象差し替え可
2. 昇格候補が出たら、月次 `/harness-review` の B 項で以下を検討:
   - H3(BLOCK)フック化: `.claude/hooks/` に fail-closed(exit2)ガードを追加 + `test-hooks.sh` に block/pass 両ケース
   - もしくは rule 文言の強化 / 決定論ゲート（`--check` を CI/routine に組込み）
3. rule 行総数が増え続ける場合は resident-slim（同 CLI）と併せて常駐圧縮を検討

## 決定論 --check（コマンドと exit の意味）

```
python3 scripts/harness-lint.py rule-promote --self-test
```

- **exit 0** = self-test PASS: hermetic temp fixture（advisory×再発 bullet 1 件=候補1 / 通常 bullet=非候補 / rules dir 欠如=観測継続 exit0）で抽出ロジックが期待通り。
- **exit 非0** = 抽出ロジックが期待とずれている（assertion 失敗を stderr に列挙）。

通常運用の gate は `python3 scripts/harness-lint.py rule-promote --check`（対象あり かつ 昇格候補>0 で exit1・対象欠如は exit0）。self-test は実源に一切触れず temp fixture のみで検証し、fixture は実行後に削除する。

## 参照

- 実装: `scripts/harness-lint.py`（`run_rule_promote` / `render_rule_promote` / `selftest_rule_promote`・サブコマンド `rule-promote`）
- 入力: `docs/*.md`（常駐ルール）/ `docs/incidents.md`（advisory 記述）
- 昇格先: `.claude/hooks/`（H3 BLOCK フック）/ `docs/incidents.md` / `/harness-review`
- 姉妹 skill: rule-promotion-extract（`scripts/rule-promotion.py`・advisory-fires.jsonl ベース）
- 設計原則: 原則 P1（自己申告禁止・決定論ゲート）/ 空≠ゼロ件
