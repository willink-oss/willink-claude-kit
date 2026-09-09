---
name: retry-failure-classifier
description: 対象リポジトリ のローカル tool ログ（.claude/logs/*-tools.jsonl）から tool の失敗/リトライ（exit_code 非空非0）を抽出し、多発している tool を分類する。トリガー語彙: リトライ分類, 失敗分類, retry classifier, tool 失敗集計, どの tool がよく失敗, exit_code 集計, 失敗多発 tool
---

# Retry / Failure Classifier（A04）

> tool 実行の失敗（非0 exit）を tools.jsonl から機械的に集計し、「どの tool がよく失敗しているか」を分類する観測 skill。月次 /harness-review・自走ループ監視の一次資料。
> エンジンは `scripts/agentlog.py` の `retry` サブコマンド（実データ集計・ハードコード成功なし）。

## 目的

- 対象リポジトリ が日々吐く gitignore 済みローカルログ `.claude/logs/*-tools.jsonl` を横断し、`exit_code` が **非空かつ '0' 以外**のイベントを失敗/リトライとして抽出する。
- 失敗を tool 名で分類（`by_tool`）し降順に並べ、**多発 tool**を可視化する。
- 空 `exit_code`（＝データ無）は失敗にカウントしない。取得失敗を「0 件」と断定しない（空≠ゼロ件・原則 P1 自己申告禁止）。

## 実行手順

1. 人間可読サマリ:
   ```
   python3 scripts/agentlog.py retry
   ```
   出力: `events`（総イベント数）／`exit_code 記録あり`／`失敗(非0)`／`failures by tool`（降順）。

2. 機械可読（standup 記録・パイプライン用）:
   ```
   python3 scripts/agentlog.py retry --json
   ```
   キー: `source_ok` / `total_events` / `with_exit_code` / `total_failures` / `by_tool`。

3. ログ源の差し替え（テスト・別ディレクトリ調査）: `--log-dir DIR`。

## ログ源が無い時の扱い（観測継続）

- `.claude/logs/*-tools.jsonl` は gitignore 済みでローカルのみ。**fresh checkout には存在しない**。
- 源が無い時は `source_ok=false` となり「観測継続（データ無）」を印字して **exit 0**（壊れ扱いにしない）。
- exit_code が記録されていない環境では `with_exit_code=0` となり「失敗/リトライ検出なし」を表示する。これはログに exit_code フィールドが乗り始めるまでの想定挙動であり、観測を継続する。

## 決定論ゲート（--check 相当・自己申告禁止）

停止/完了判定は自己判断でなく機械的 self-test で行う（原則 P1/P2）。本 skill の決定論ゲートは:

```
python3 scripts/agentlog.py retry --self-test
```

- temp fixture で hermetic に検証（実源には触れない）。
- 検証内容: 正常 fixture の期待集計（failures=2 / by_tool={Bash:1, Edit:1} / with_exit_code=3）と、源欠如時の `source_ok=false` + 「観測継続」レンダリング。
- 全 pass で `retry self-test: PASS` を印字し **exit 0** / 1 つでも fail で **exit 1**。

全サブコマンド一括検証は `python3 scripts/agentlog.py --self-test`。

## 参照

- エンジン: `scripts/agentlog.py`（`run_retry` / `render_retry` / `selftest_retry`・単一ファイル観測 CLI）
- 姉妹 skill: `harness-review`（本バンドル外）（月次レビューで本ゲートを実走）
- 設計原則: 空≠ゼロ件・原則 P1（自己申告禁止）
