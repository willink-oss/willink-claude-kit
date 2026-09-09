---
name: trigger-vocab-dedup
description: 各 SKILL.md の description から「トリガー語彙」を抽出し、複数 skill が同じトリガー語を主張する衝突を決定論的に検出して割当最適化を促す。トリガー語彙: トリガー語彙衝突, trigger dedup, 語彙衝突, skill トリガー重複, トリガー割当, skill routing 衝突, 発火語重複
---

# trigger-vocab-dedup（skill 間トリガー語彙の衝突検出）

> skill は description の「トリガー語彙: a, b, c」で発火する。複数 skill が**同じトリガー語**を主張すると、
> どちらが起動すべきか曖昧になり誤 routing（過少発火 / 誤発火）を招く。
> 全 SKILL.md の frontmatter からトリガー語を抽出して所有 skill を突合し、
> 2 skill 以上が同一語を主張する衝突を機械検出する（原則 P1 自己申告禁止・決定論ゲート）。

## 目的

- 各 `skills/*/SKILL.md` の frontmatter description から「トリガー語彙: …」列を抽出する
- トリガー語 → 所有 skill の写像を作り、**同一トリガー語を 2 skill 以上が主張する衝突**を列挙する
- 衝突が出たら、どちらか一方に語を寄せる／より具体的な語に差し替える等の**割当最適化**を促す
- skills dir 欠如 or SKILL.md 0 件は「対象無・観測継続」として exit0（欠如≠違反・空≠ゼロ件）

## 衝突は「1 語 = 1 主所有」を目標に寄せる

- トリガー語が複数 skill にまたがると、LLM の routing が非決定的になる（どちらも発火し得る／どちらも発火しない）。
- 衝突語は **主所属の skill に一本化**するか、各 skill 側でより固有な語（例: 汎用「レビュー」→「PR レビュー」「常駐ルールレビュー」）に差し替えて解消する。
- 解消は「語を減らす／具体化する」方向のみ。曖昧な包括語を増やして衝突を増やさない（Counter-Convergence: 要求外の追記禁止）。

## 実行手順

1. `python3 scripts/harness-lint.py trigger-dedup` を実走し、トリガー語総数と衝突件数を確認する
   - 入力: `skills/*/SKILL.md`（`--skills-dir DIR` で差替可・`--root DIR` で repo ルート差替可）
   - `--json` で機械可読出力（`total_triggers` / `items[]`=衝突語と所有 skill 一覧）
   - 出力例: `⚠ '<衝突語>' → skillA, skillB`
2. 衝突が出たら、各衝突語について主所属 skill を 1 つ決め、他 skill 側の description から当該語を除く／具体化する
   - 語の意味が本当に共有なら「上位 skill に集約し下位から削除」、意味が別なら「各自を固有語に差し替え」
3. `--check` で違反 0 を確認してから SKILL.md をコミットする（`python3 scripts/harness-lint.py trigger-dedup --check`）
4. 月次 `/harness-review` の F 項（Skills Disclosure）で本 check の exit と衝突件数を記録する

## 決定論 --check（コマンドと exit の意味）

```
python3 scripts/harness-lint.py trigger-dedup --self-test
```

- **exit 0** = self-test PASS: hermetic temp fixture（skill a/b が「共有語」を共有し「固有A/固有B」は非衝突）で
  衝突検出ロジックが期待通り（衝突 1 件・衝突語=「共有語」を assert）。加えて skills dir 欠如で
  「対象無・観測継続」exit0 になる耐性も確認する。fixture は tempfile に作り実行後に削除する（リポジトリに残さない）。
- **exit 非0** = FAIL: 抽出・衝突集計・対象欠如耐性のいずれかが壊れている（stderr に失敗内容を列挙）。

運用時の違反ゲート:

```
python3 scripts/harness-lint.py trigger-dedup --check
```

- **exit 0** = 対象あり かつ 衝突 0（または対象欠如）
- **exit 1** = 対象あり かつ 衝突 > 0（衝突語と所有 skill を stdout に印字）

## 参照

- 実装: `scripts/harness-lint.py` サブコマンド `trigger-dedup`（`run_trigger_dedup` / `_extract_triggers` / `_parse_frontmatter`）
- 検査対象: `skills/*/SKILL.md` frontmatter の description「トリガー語彙: …」
- 姉妹 skill: skill-desc 系（description 健全性）/ resident-context-budget / rule-promotion-extract（同じ決定論ゲート系列）/ harness-review（月次 F 項）
- 設計原則: 原則 P1（自己申告禁止・決定論ゲート・閾値は緩めない）/ ハーネス再設計 H1（Skills Disclosure 最小化）
