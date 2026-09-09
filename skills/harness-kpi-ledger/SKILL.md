---
name: harness-kpi-ledger
description: ハーネス KPI（自然言語ルール残数・advisory/blocking フック数・CI required check 数）を決定論的に計測し、ledger 行として追記して月次の退行を検知する。トリガー語彙: ハーネス KPI, KPI 計測, kpi ledger, ルール残数計測, フック数計測, KPI 退行検知, harness kpi
---

# harness-kpi-ledger（ハーネス KPI 計測 + ledger 追記 + 退行検知）

> `scripts/govern.py kpi` サブコマンドの skill ラッパ。月次 `/harness-review` の KPI 節（`docs/principles.md`）に対応する観測 skill。
> harness-engineering 戦略（原則 P1）の KPI は「自然言語ルール残数 → 減」「CI required check 数 → 増」「advisory→blocking 昇格数 → 増」の 3 指標。
> これらを**文書に書いた目標値ではなく実ファイルの実測値**で毎月計測し、前月 ledger と突合して**悪化（退行）を機械的に検知**する（原則 P1 自己申告禁止・決定論ゲート）。

## 目的

- `docs/*.md` の自然言語ルール（`- **…**` 箇条書き）数・行数・ファイル数を機械集計する
- `.claude/hooks/*.sh` を `exit 2`（fail-closed）有無で **blocking / advisory** に分類し数える
- `docs/principles.md` の KPI 表の宣言値を併記し、実測値と宣言値の乖離を見えるようにする
- `--use-gh` で全社 default branch の required status check 数を best-effort 計測する
- 計測結果を 1 行 JSON の ledger として追記し、前行との差分で退行（ルール増 / 昇格数減 / required check 減）を検知する
- rules_dir / harness_doc いずれも欠如なら「対象無・観測継続」で exit0（欠如は失敗ではない・空≠ゼロ件）

## 姉妹 skill との棲み分け

- **本 skill（harness-kpi-ledger）**: KPI 3 指標を **1 スナップショットに集約**し ledger 化 → 月次の**時系列トレンド / 退行**を見る。
- **resident-context-budget**: 常駐 context 行数の**予算 floor ゲート**（超過で exit1）。KPI「ルール残数 → 減」の圧力を毎回かける側。
- **rule-promote-audit / rule-promotion-extract**: advisory ルール／発火ログから **H3 昇格候補**を抽出する側（KPI「昇格数 → 増」の供給源）。
- 本 skill はこれら個別ゲートの成果を **KPI 数値として毎月まとめて記録**する集計レイヤ。

## 実行手順

1. `python3 scripts/govern.py kpi` を実走し人間可読サマリを確認する
   - 出力: 自然言語ルール数 / rule files / lines・hooks（advisory/blocking）・CI required checks・KPI 表宣言値・末尾 `LEDGER {json}`
   - 全社 required check も計測するなら `python3 scripts/govern.py kpi --use-gh`（gh 未認証・取得不可時は `ci_required_checks=null` で「観測継続」note を付す）
2. ledger 行を追記する（durable・git 追跡対象）:
   - `python3 scripts/govern.py kpi --json >> <KNOWLEDGE_DIR>/harness-kpi-ledger.jsonl`
   - `--json` は ledger dict のみを 1 行で出すので、そのまま `.jsonl` に append できる
   - ⚠️ `.claude/logs/` は gitignore 実行時領域なので ledger 置き場に使わない（時系列比較には git 追跡パスを使う）
3. 退行検知: 追記した最新行を直前の月次行と突合し、以下を悪化として `/harness-review` の KPI 節に記録する:
   - `nl_rules` / `nl_rule_lines` が**増加**（KPI 方向 = 減 に逆行）→ resident-context-budget / rule-promote-audit で削減を検討
   - `blocking_hooks` が**減少** or `advisory_hooks` だけ増える（KPI 方向 = 増＝昇格が進んでいない）→ rule-promotion-extract で昇格候補を起票
   - `ci_required_checks` が**減少**（null→数値化できない場合は「未計測」と明記し退行断定しない）→ branch protection の required check 解除は Level 3 なので 責任者 エスカレーション
4. 月次 `/harness-review` の KPI 節に「今月の 3 指標 / 前月差 / 退行有無」を記録し、`harness-engineering.md` の KPI 表宣言値を実測に合わせて更新する（宣言値を実測より緩く据え置かない）

## 決定論 --check（コマンドと exit の意味）

```
python3 scripts/govern.py kpi --self-test
```

- **exit 0** = self-test PASS: hermetic temp fixture（rules 2 bullet / hooks = blocking 1 + advisory 1 / KPI 表あり → `nl_rules=2` `blocking_hooks=1` `advisory_hooks=1` `kpi_doc` 非空 を assert、かつ全 dir 欠如 → `status=observe`）で計測ロジックが期待通り。fixture は tempfile に作り実行後削除する（実源に一切触れない）。
- **exit 非0** = 計測ロジックが期待とずれている（`[FAIL] kpi: …` を stdout に印字）。

補足: `kpi` は違反ゲートではなく**計測（status=measured）**なので、通常実行の exit は常に 0。退行の判定は手順 3 の ledger 突合（人間可読 or 別スクリプト）で行い、skill 自体の健全性ゲートが本 `--self-test`。

## 参照

- 実装: `scripts/govern.py`（`compute_kpi` / `_parse_kpi_table` / `_gh_required_checks` / `st_kpi`・サブコマンド `kpi`）
- 計測入力: `docs/*.md`（自然言語ルール）/ `.claude/hooks/*.sh`（advisory=非exit2 / blocking=exit2）/ `docs/principles.md`（KPI 表宣言値）
- ledger 追記先: `<KNOWLEDGE_DIR>/harness-kpi-ledger.jsonl`（git 追跡・時系列比較用。`.claude/logs/` は使わない）
- 記録先: 月次 `/harness-review`（`harness-review`（本バンドル外））の KPI 節
- 姉妹 skill: resident-context-budget（ルール行数予算）/ rule-promote-audit・rule-promotion-extract（昇格候補抽出）
- 設計原則: 原則 P1（自己申告禁止・決定論ゲート・閾値を緩めない）/ `docs/principles.md`（KPI 3 指標と方向）
