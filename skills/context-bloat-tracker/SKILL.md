---
name: context-bloat-tracker
description: transcript の session 別メッセージ数・近似入力量を集計し、context が肥大した session を決定論的に検出する。トリガー語彙: context 肥大, context bloat, session 肥大検出, 入力量推移, transcript 集計, context diet, 肥大 session, コンテキスト汚染
---

# context-bloat-tracker（session context 肥大トラッカー）

> harness-engineering の「常駐 context 最小化」（2026-06-10 H1）を **session ランタイム側**から補完する観測 skill。
> 常駐 context の静的行数は resident-context-budget が測るが、実際に 1 ターンへ載る量は
> セッション中の履歴蓄積・tool 結果で膨れる。transcript から session 別の入力量を実測し、
> 肥大した session を機械検出して「`/compact` 提案・new task = new session」の判断材料にする（原則 P1 自己申告禁止）。

## 目的

- `~/.claude/projects/*/*.jsonl`（実 transcript）を session 別に集計し、メッセージ数と近似 char 量を測る
- 閾値超過（msgs ≥ 200 **or** chars ≥ 400,000）の session を `*BLOAT*` として列挙する
- context 肥大が習慣化している session パターンを可視化し、`/compact` 頻度・session 分割の運用改善に供する
- ログ源（transcript）が無い環境では「観測継続」として exit0（観測不足は失敗ではない）

## 実行手順

1. 集計を表示する:
   ```
   python3 scripts/agentlog.py context-bloat          # 人間可読サマリ
   python3 scripts/agentlog.py context-bloat --json    # 機械可読（JSON）
   ```
   - 既定源: `~/.claude/projects/*/*.jsonl`（`--transcript <path>` で単一ファイル指定・`--projects-dir` で差替可）
   - 出力: sessions 総数 / bloated 件数 / char 降順の top10 session（`*BLOAT*` フラグ付き）
   - 集計単位: `type` が `user`/`assistant` のレコードを 1 メッセージとして数え、各 content block の
     text/thinking 文字数・tool_use input・tool_result を近似 char として合算する（`compact_boundary` も計数）
   - **源が無い時（transcript 未発見）**: `観測継続` と表示して exit0。これは壊れではなく「まだ測る材料がない」状態。
     空出力を「0 件」と即断しない（`docs/incidents.md` 空≠ゼロ件）。
2. bloated session が継続的に出るなら運用側で対処:
   - 長時間 session は `/compact`（hint 付き）を提案／タスク切替時は new session 起動（`docs/practices.md`）
   - 常駐 context 側の肥大は姉妹 skill resident-context-budget の `--check` で別途 floor 管理
3. 月次 `/harness-review` の E 項（生産性）で bloated 率の推移を記録する。

## 決定論 --check（コマンドと exit の意味）

```
python3 scripts/agentlog.py context-bloat --self-test
```

- **exit 0** = self-test PASS: hermetic temp fixture（250 メッセージの `big` session＝BLOAT / 3 メッセージの
  `small` session＝非 BLOAT / char 降順で `big` が先頭 / 欠如パスは source_ok=False かつ render に「観測継続」）で
  集計・判定・欠如耐性が期待通り。fixture は tempfile に作り実行後に削除する（リポジトリに残さない）。
- **exit 非0** = FAIL: 集計ロジックまたは源欠如耐性が壊れている（stderr に失敗内容）。

閾値（`scripts/agentlog.py` の module 定数）: `BLOAT_MSG_THRESHOLD=200` / `BLOAT_CHAR_THRESHOLD=400,000`。
原則 P1 の floor 運用に従い、緩める方向の変更は禁止（下げる／据え置きのみ）。

## 参照

- 実装: `scripts/agentlog.py` サブコマンド `context-bloat`（`run_context_bloat` / `render_context_bloat` / `selftest_context_bloat`）
- 集計源: `~/.claude/projects/*/*.jsonl`（各 Claude Code session の transcript）
- 姉妹 skill: resident-context-budget（常駐 context 静的行数 floor）/ harness-review（月次 E 項）
- 運用ルール: `docs/practices.md`（compact / new session）/ `docs/incidents.md`（空≠ゼロ件）
- 設計原則: 原則 P1（自己申告禁止・決定論ゲート）/ ハーネス再設計 H1（context 最小化）
