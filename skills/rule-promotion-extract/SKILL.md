---
name: rule-promotion-extract
description: advisory フックの同種 warn 再発回数を集計し、閾値以上を H3(blocking)昇格候補として抽出する。トリガー語彙: 昇格候補, rule promotion, advisory 昇格, blocking 昇格, 再発集計, H3 昇格, common-mistakes 昇格
---

# rule-promotion-extract（advisory→blocking 昇格候補抽出）

> harness-engineering KPI **「advisory→blocking 昇格数 0/7 → 増」** に紐づく観測 skill。
> advisory フックは fail-open(exit0)で通知するだけなので、同じ warn が繰り返し出ても止められない。
> 同種 warn の**再発回数**が閾値(既定 2)以上なら、その逸脱は習慣化しており H3(BLOCK フック)
> または `common-mistakes.md` への昇格を検討すべき決定論的証拠になる（原則 P1 自己申告禁止）。

## 目的

- A03 (advisory-fire-tally) が読むのと同じ `advisory-fires.jsonl` を消費し、warn 別再発回数を集計
- `--recur N`(既定 2)以上の warn を**昇格候補**として列挙する
- ログ欠如/空は「候補 0・観測継続」として exit0（観測不足は失敗ではない）

## 実行手順

1. `python3 scripts/rule-promotion.py`（既定 `--recur 2`）を実行し昇格候補を確認
   - 入力: `.claude/logs/advisory-fires.jsonl`（`ADVISORY_LOG_FILE` で差替可）
   - `--json` で機械可読出力（total_fires / candidates[] 等）
   - `--recur N` で閾値変更（例: `--recur 3` でより保守的に）
2. 候補が出たら、月次 `/harness-review` の B 項で以下を検討:
   - H3(BLOCK)フック化: `.claude/hooks/` に fail-closed(exit2)ガードを追加 + `test-hooks.sh` に block/pass 両ケース
   - ルール昇格: `docs/incidents.md` に 1 行追記（経緯は `<KNOWLEDGE_DIR>/mistake-log-archive.md`）
3. 昇格したら該当 advisory の過検出率を再観測し、閾値調整の要否を判断

## 決定論 --check（コマンドと exit の意味）

```
python3 scripts/rule-promotion.py --self-test
```

- **exit 0** = self-test PASS: hermetic temp fixture（同一 warn 2 回=候補 / 別 warn 1 回=非候補 / 破損・空行=除外 / 欠如ログ=候補0 exit0）で抽出ロジックが期待通り
- **exit 非0** = 抽出ロジックが期待とずれている（assertion 失敗を stderr に列挙）

fixture は tempfile に作成し実行後に削除する（リポジトリに残さない）。

## 参照

- 入力生成元: `.claude/hooks/post-commit-verify.sh`（advisory-fires.jsonl 追記）
- 姉妹 skill: A03 advisory-fire-tally（`scripts/advisory-tally.py`・rule 別集計）
- 昇格先: `docs/incidents.md` / `.claude/hooks/` / `/harness-review`
- 設計原則: 原則 P1（自己申告禁止・決定論ゲート）
