---
name: session-length-compact-audit
description: transcript を session 別に集計し、session 長（メッセージ数）と compact 発生回数を実測して衛生違反（長すぎる session・compact 多発）を抽出する。トリガー語彙: session 長監査, compact 頻度, session 衛生, session hygiene, compact 回数, session 分割, /compact 過多, 長時間 session
---

# session-length-compact-audit（session 長 × compact 頻度監査）

> `docs/practices.md`（「New task = new session」「/compact は hint 付きで」）を **実測側**から検証する観測 skill。
> ルールは「長い session は分割・compact は都度」と定めるが、実際に session がどれだけ伸び／何回 compact したかは
> transcript を集計しないと分からない。本 skill は session 別のメッセージ数と compact 回数を機械集計し、
> 衛生違反（長すぎる session・compact 多発 session）を可視化して session 分割・compact 運用の改善材料にする（原則 P1 自己申告禁止）。

## 目的

- `~/.claude/projects/*/*.jsonl`（実 transcript）を session 別に集計し、**メッセージ数（session 長）** と **compact 回数** を実測する
- compact は `type=system` かつ `subtype=compact_boundary`、または `isCompactSummary` を持つレコードを 1 回として数える
- session 長降順で並べ、長時間・compact 多発の session を上位に列挙する（衛生違反の抽出）
- 「New task = new session」が守られず 1 session に多タスクが載って伸びる／`/compact` 頻発でコンテキストが安定しない
  パターンを検出し、session 分割・`/compact` hint 運用の改善に供する
- ログ源（transcript）が無い環境では「観測継続」として exit0（観測不足は失敗ではない）

## 実行手順

1. 集計を表示する:
   ```
   python3 scripts/agentlog.py session-hygiene          # 人間可読サマリ
   python3 scripts/agentlog.py session-hygiene --json    # 機械可読（JSON）
   ```
   - 既定源: `~/.claude/projects/*/*.jsonl`（`--transcript <path>` で単一ファイル指定・`--projects-dir` で差替可）
   - 出力: session 総数 / session 長降順の top15（`msgs=<メッセージ数> compacts=<compact 回数> <sessionId>`）
   - 集計単位: `type` が `user`/`assistant` のレコードを 1 メッセージとして数える。compact は
     `compact_boundary` システムレコードと `isCompactSummary` レコードの合算。
   - **源が無い時（transcript 未発見）**: `観測継続` と表示して exit0。これは壊れではなく「まだ測る材料がない」状態。
     空出力を「0 件」と即断しない（`docs/incidents.md` 空≠ゼロ件）。
2. 衛生違反が継続的に出るなら運用側で対処:
   - 突出して長い session（上位の msgs）→ タスク切替時に new session を起動する（`docs/practices.md`）
   - compact が多発する session → `/compact` を hint 付きで早めに打つ／そもそもタスクを分割する
   - 近似入力量（chars）ベースの肥大は姉妹 skill context-bloat-tracker の `--check`（閾値 msgs≥200 / chars≥400,000）で別途管理
3. 月次 `/harness-review` の E 項（生産性）で長時間 session 率・compact 多発 session の推移を記録する。

## 決定論 --check（コマンドと exit の意味）

```
python3 scripts/agentlog.py session-hygiene --self-test
```

- **exit 0** = self-test PASS: hermetic temp fixture（session `s1` に user+assistant+compact_boundary+isCompactSummary、
  session `s2` に user 1 件）で `sessions=2` / `s1` の `messages=3` / `s1` の `compacts=2` / 欠如パスは source_ok=False かつ
  render に「観測継続」を機械検証する。fixture は tempfile に作り実行後に削除する（リポジトリに残さない）。
- **exit 非0** = FAIL: session 長・compact カウント集計または源欠如耐性が壊れている（stderr に失敗内容）。

集計ロジックは姉妹 skill context-bloat-tracker と共通の `_collect_sessions`（`scripts/agentlog.py`）を用いる。
原則 P1 の floor 運用に従い、判定を甘くする方向の変更は禁止（据え置き／厳格化のみ）。

## 参照

- 実装: `scripts/agentlog.py` サブコマンド `session-hygiene`（`run_session_hygiene` / `render_session_hygiene` / `selftest_session_hygiene`・集計は共通 `_collect_sessions`）
- 集計源: `~/.claude/projects/*/*.jsonl`（各 Claude Code session の transcript）
- 姉妹 skill: context-bloat-tracker（近似入力量ベースの肥大検出）/ harness-review（月次 E 項）
- 運用ルール: `docs/practices.md`（New task = new session / compact hint）/ `docs/incidents.md`（空≠ゼロ件）
- 設計原則: 原則 P1（自己申告禁止・決定論ゲート）/ ハーネス再設計 H1（context 最小化）
