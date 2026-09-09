---
name: codex-spark-delegate
description: Claude Code または Codex から、完了条件が明確な小規模・局所・低リスクのコード編集、テスト追加、UI微調整、定型変換を GPT-5.3-Codex-Spark の独立 usage lane に委譲し、通常の Codex / Work 使用枠を温存する。トリガー語彙: Spark 委譲, Codex Spark, GPT-5.3-Codex-Spark, 軽量実装, 小規模修正, UI微調整, 別枠モデル, rate limit, レート制限, 通常枠を温存
allowed-tools: Bash, Read, Glob, Grep
---

# codex-spark-delegate

> 実装: `scripts/codex-spark.sh`
> OpenAI Docs: https://learn.chatgpt.com/docs/agent-configuration/speed
>
> Codex-Spark は Fast mode ではなく、独自の usage limits を持つ別モデル。親の品質責任を移さず、軽量な実行だけを別 lane に逃がす。

## 先に判定する

次の条件を**すべて**満たす独立スライスなら、通常モデルで直接実装する前に Spark 委譲を優先する。

- 完了条件、対象パス、焦点を絞った検証コマンドを自己完結した prompt に書ける
- 目安 1〜3 ファイルの小差分で、既存設計・API・データフローを変えない
- UI 微調整、単純なテスト追加、定型変換、ボイラープレート、明白な局所修正のいずれか
- 親が委譲後に `git diff` と実テストを再検証できる
- 同じファイルを別 agent / process が同時編集していない

次のどれかに当たれば**使わない**。

- 1〜2 回の Read / Grep だけで終わる（起動コストの方が大きい）
- 要件が曖昧、設計・ADR・事業判断・複雑なデバッグ・長期自走が必要
- security、認証、secret、権限、課金、法務、本番データ、deploy、外部 write を含む
- Level 3、破壊操作、広範なリファクタ、複数リポジトリ、画像入力を含む
- 現在の worker 自身が Spark（nested delegation は禁止）

## 実行手順

1. ユーザーへ Spark skill を使う理由と委譲範囲を短く伝える。
2. まず読み取り専用で足りるか判断する。編集が必要な場合だけ `--write` を付ける。
3. 対象ファイル、禁止事項、受け入れ条件、検証コマンドを prompt に明記して 1 回だけ実行する。
4. 親が `git status` / `git diff` を読み、検証コマンドを自分で再実行する。
5. unavailable / quota / access error なら再試行ループにせず、親の現在モデルで継続する。通常 Codex 子プロセスへの自動 fallback は起動しない。

```bash
# 読み取り専用の提案・局所調査
scripts/codex-spark.sh --read-only -- \
  "対象: src/foo.ts。重複分岐を減らす最小パッチ案を提示。変更は禁止。検証: npm test -- foo"

# 小規模編集（親が後で diff と test を再検証する）
scripts/codex-spark.sh --write -- \
  "対象: src/foo.ts と src/foo.test.ts のみ。既存APIを変えず境界値テストを追加。検証: npm test -- foo。commit禁止。"
```

## 安全境界

- ラッパーはモデルを `gpt-5.3-codex-spark`、bounded task の reasoning effort を `low` に固定し、外部からのモデル指定を受け付けない。
- 既定は `read-only`。編集は明示した `--write` の `workspace-write` だけ。
- `--ephemeral` で子セッションを保存せず、`CODEX_SPARK_ACTIVE=1` で再帰委譲を拒否する。
- Spark の返答や exit 0 は完成証拠ではない。差分・テスト・実ファイルを親が検証する。
- 使用枠削減量は自己申告しない。実際の残量は Codex の Usage 画面で確認する。

## 決定論 self-test

ネットワークやモデル枠を消費せず、モデル固定・sandbox・再帰ガードを検証する。

```bash
bash scripts/codex-spark.sh --self-test
```
