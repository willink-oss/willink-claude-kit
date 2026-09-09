---
name: subagent-usage-analyzer
description: transcript から Agent（subagent）起動を抽出し、type 別件数と子 tool_use<=2 の過剰起動（過少委譲）を決定論的に集計する。トリガー語彙: subagent 分析, Agent 起動集計, 過剰起動, 過少委譲, subagent usage, サブエージェント乱用, subagent-guidelines 検証
---

# subagent-usage-analyzer（Agent 起動の過剰/過少判定）

> `docs/practices.md`（BP Pattern 2B）の遵守を **文書自己申告でなく transcript 実測**で裏付ける観測 skill。
> 「単一 Read / 1〜2 回の Grep に Agent を使わない」というルールが守れているかを、
> 実際の Agent 起動ログから機械集計する（原則 P1 自己申告禁止・決定論ゲート）。

## 目的

- transcript 内の `Agent` tool_use を抽出し、`subagent_type` 別の起動件数を集計する
- sidechain（子 transcript）記録がある場合、Agent の**子 tool_use が 2 以下**の起動を「過剰起動疑い（過少委譲）」として列挙する
  — 単一 Read や 1〜2 回 Grep 程度の仕事に Agent を割いた兆候（`subagent-guidelines.md` の ❌ Don't Use）
- Agent 起動の分布（general-purpose に偏っていないか・Explore/Plan の使い分け）を可視化し、委譲設計の見直し材料にする

## 過剰起動 heuristic と観測継続

- **子 tool_use<=2** を過剰起動疑いの決定論しきい値とする（子仕事が小さすぎ＝自分でやれた可能性）。
- **重要（源欠如 ≠ 0 件）**: transcript が見つからない、または sidechain 記録が無い場合、
  過剰起動 heuristic は**適用不可**として「観測継続」と明示する（exit0・壊れではない）。
  子 tool_use が数えられないことを「過剰起動 0 件」と即断しない（空≠ゼロ件）。
  この場合でも Agent 起動総数と type 別内訳は集計できるので、分布だけは報告する。

## 実行手順

1. 集計を表示する:
   ```
   python3 scripts/agentlog.py subagent          # 人間可読サマリ
   python3 scripts/agentlog.py subagent --json    # 機械可読（JSON）
   ```
   - 源: `~/.claude/projects/<proj>/*/*.jsonl` transcript（`--transcript <path>` で単一指定も可）
   - 出力: Agent 起動総数 / type 別件数（降順）/ 子 tool_use<=2 の過剰起動疑い一覧
   - transcript 未発見 → `観測継続` と表示（exit0）。sidechain 無し → heuristic 適用不可（観測継続）と明示。
2. 過剰起動疑いが出たら、対象タスクが本当に委譲すべきものだったかを `subagent-guidelines.md` に照らして判断:
   - 単一ファイル操作 / 1〜2 回の検索 / 小規模な孤立変更 → 直接 Read/Grep/Edit に切替（❌ Don't Use）
   - 10 files 以上の探索 / 独立並列 / メインコンテキスト保護 → 委譲は妥当（✅ Use）
3. type 別分布が general-purpose に偏っている場合、Explore/Plan/claude-code-guide への使い分け（タイプ選択早見表）を再確認する
4. 月次 `/harness-review` の E 項（生産性）に集計と過剰起動疑い件数を記録する

## 決定論 --check（コマンドと exit の意味）

```
python3 scripts/agentlog.py subagent --self-test
```

- **exit 0** = self-test PASS: hermetic temp fixture で判定ロジックが期待通り。
  fixture は Agent 起動 2 件（Explore=子 tool_use 1・過少 / Plan=子 tool_use 3・正常）を含み、
  `total_agents=2` / `by_type={Explore:1,Plan:1}` / `over_launch_count=1`（Explore が過剰起動疑い）を検証。
  さらに sidechain 無し transcript（heuristic 適用不可・`over_launch_count=None`）と
  欠如ログ（`source_ok=False`・render に「観測継続」）の耐性も確認する。
- **exit 非0** = FAIL: 集計・heuristic・源欠如耐性のいずれかが壊れている（stderr に失敗内容）。

fixture は tempfile に作り実行後に必ず削除する（hermetic・実源には一切触れずリポジトリにも残さない）。

## 参照

- 実装: `scripts/agentlog.py`（サブコマンド `subagent`・`run_subagent` / `_analyze_subagents_file`）
- 判定基準: `docs/practices.md`（✅ Use / ❌ Don't Use・タイプ選択早見表）
- 姉妹 skill: harness-review（月次 E 項）/ advisory-fire-tally / resident-context-budget（同じ agentlog / 決定論ゲート系列）
- 設計原則: 原則 P1（自己申告禁止・決定論ゲート）/ ブログ BP Pattern 2B（Let Claude Manage Its Own Context）
