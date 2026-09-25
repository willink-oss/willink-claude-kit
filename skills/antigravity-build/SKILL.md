---
name: antigravity-build
description: i-Willink 標準開発フローを Antigravity で実行するための 5 phase adapter。Claude Code の /build と同期し、Antigravity の Planning Mode やサブエージェント動的生成と融合する。
---

# antigravity-build — Antigravity Adapter For The 5 Phase Flow

このスキルは、`commands/build.md` で定義されている Claude Code 用の `/build`（5フェーズ開発フロー）を、Antigravity（Google DeepMind開発の自律型コーディングエージェント）の動作モデルに適応させるためのアダプターです。
Claude Code 用のファイルが常に正本（Source of Truth）であり、本ファイルはツールや動作特性の差分を埋めるためのものです。

---

## 1. ツールマッピング

Claude Code のロール契約（`agents/` 配下）で指定されているツールは、Antigravity では以下のようにマッピングして使用してください。

| Claude Code ツール | Antigravity ツール | 役割と制約 |
| :--- | :--- | :--- |
| **`Read`** | `view_file` | ファイル内容の読み取り。 |
| **`Glob` / `Grep`** | `grep_search` | ファイルの検索。 |
| **`Bash`** | `run_command` | コマンドの実行。読取専用サブエージェントは破壊的コマンドの実行を禁止。 |
| **`Edit` / `Write`** | `replace_file_content`<br>`multi_replace_file_content`<br>`write_to_file` | ファイルの変更および新規作成。 |
| **`Agent`** | `define_subagent`<br>`invoke_subagent` | サブエージェントの動的定義と実行。 |

---

## 2. 正本ファイルの読み込み

開発開始時および検証時に、以下のファイルを必ず読み込んでください。

- 共通開発標準: [skills/dev-standards/SKILL.md](../dev-standards/SKILL.md)
- UI を触る場合の a11y 標準: [skills/a11y-standards/SKILL.md](../a11y-standards/SKILL.md)
- プロジェクト固有標準（存在する場合）: `.claude/skills/project-standards/SKILL.md`
- レビュー用メモリ（存在する場合）: `.claude/agent-memory/dev-reviewer/MEMORY.md`

---

## 3. 5フェーズフローと Antigravity の動作マッピング

### 人とのやり取り（全 phase 共通）
- 着手前に 1 行の計画、終わりに短いまとめ（何を変えたか・何で確かめたか（コマンドと exit code）・人に必要なこと）を書きます。
- 途中の状況メモは次の操作と同じメッセージに書き、入力が要らない段で止まって確認を求めません。止まるのは、人の判断なしに進めない時と、取り返しのつかない操作（削除・force push・リポ外の変更）の前だけです。

### Phase 1: 影響範囲探索
- **起動条件**: タスクの影響範囲が3軸以上独立している場合のみ実行。1〜2軸の場合はメインセッションで直接探索します。
- **Antigravityでの実行方法**:
  - `define_subagent` を用いて、 `agents/dev-explorer.md` をベースとした `dev-explorer` サブエージェントを動的に定義します。
  - `invoke_subagent` で並行起動し、完了通知が届くまでメイン側は他の独立した調査を進めます。
  - レポートは `dev-explorer` の出力フォーマット（Summary, Key files, Conventions observed, Open questions, Recommended follow-up）を厳守させます。

### Phase 2: 実装計画
- **起動条件**: 変更が50行以上または複数ファイルにまたがる場合に実行。軽微な修正はスキップします。
- **UI を触る場合**: `implementation_plan.md` に、新規/変更する操作要素ごとの accessible name / role / state と、文字拡大 200% 時のレイアウト方針を明記します（a11y は実装後の監査ではなく計画事項）。UI 非変更なら「a11y: N/A」と書きます。
- **Antigravityでの実行方法**:
  - Antigravity の標準機能である `implementation_plan.md` アーティファクトのライフサイクルに統合します。
  - 設計上の不確実性が高い場合は、 `define_subagent` で `dev-planner` を定義・起動し、計画のドラフトを作成させます。
  - 最終的な `implementation_plan.md` はメインエージェントがまとめ、**ユーザーの明示的な承認**を得ます。

### Phase 3: 実装
- **Antigravityでの実行方法**:
  - メインの Antigravity エージェントが直接 `replace_file_content` 等を使って実装を行います。**実装作業をサブエージェントに委譲してはいけません**（Generator-Verifierの分離）。
  - 実装開始時に `task.md` アーティファクトを作成し、進捗を `[/]` や `[x]` で追跡します。

### Phase 4: 検証
- **Antigravityでの実行方法**:
  - `define_subagent` を用いて、 `agents/dev-tester.md` および `agents/dev-reviewer.md` を定義します。
  - `dev-tester` には `enable_write_tools = true` を設定してテストコマンドの実行を許可し、 `dev-reviewer` には `enable_write_tools = false`（Read-only）を設定します。
  - `invoke_subagent` で両者を並行起動します。
  - **Early Victory の防止**: `dev-tester` には必ずフルテストスイートを実行させ、一部が通っただけで PASS と報告させないようにします。 `dev-reviewer` には差分のあるすべてのファイルを確認させます。
  - **UI 差分の a11y 検証**: `dev-tester` に a11y 静的ゲート（`python3 scripts/a11y-static-check.py --root .`）と a11y テストを実行させます。**exit 3 は「走査 0 件 = 不明」で合格ではありません**。`dev-reviewer` は accessible name / role の欠落を CRITICAL として扱います。
  - **止める指摘だけ**: `dev-reviewer` の Findings は merge を止める指摘だけにし、各指摘に file:line・なぜ誤りか・落ちることの示し方（再現手順か赤くなるテスト）を必ず付け、行頭に `BLOCKER` を付けます。示せないもの・改善提案は「Not blocking」へ回します。

### Phase 5: 修正 + コミット
- **Antigravityでの実行方法**:
  - Phase 4 の検証結果に基づき、指摘されたバグやエラーをメインエージェントが修正します。
  - 修正後、最大2ループまで Phase 4 を再試行します。
  - 全て PASS と判定されたら、 `walkthrough.md` アーティファクトを作成して変更内容とテスト結果を記録し、Conventional Commits 規約に従ってコミットします。

### 割り込みからの再開（`/build --from oneshot/state.json`）
- `/oneshot` の無人実行に人が割り込んだとき（`oneshot/STOP`・Ctrl-C・escalate）の着地点です。**worktree も途中の commit も捨てず**、同じ worktree・同じブランチで続けます。
  - `status` が `running` のままなら、まず人に oneshot/STOP を置いたか確かめます（無人区間が走っている最中に同じ worktree を触らない）。
  - 最初に 1 画面で要約します（分母つき）: `goal`・未完了の dod（`result` が `green` でないもの）・`violations`・`note`・`blocker`・`interrupted`・`pr`。
  - `phase` から再開: preflight / explore / plan → Phase 2、implement / loop → Phase 3、verify / mutate → Phase 4、deliver / escalate → Phase 5。
  - 以後は人とのやり取りです。`/oneshot` の無人用の常設指示は適用しません。
  - 検証には契約の DoD をそのまま使えます（`python3 <kit>/scripts/oneshot-preflight.py round` が exit 0 なら緑）。

---

## 4. サブエージェントの動的定義用パラメータ例

Antigravity で `define_subagent` を呼び出す際は、以下の設定値を使用してください。

### dev-explorer
- `name`: `dev-explorer`
- `description`: `i-Willinkコードベース探索専門サブエージェント。読取専用。`
- `system_prompt`: `agents/dev-explorer.md` の指示文をベースに、ツールマッピングを反映させたプロンプト。
- `enable_write_tools`: `false`

### dev-planner
- `name`: `dev-planner`
- `description`: `i-Willink実装計画の立案専門サブエージェント。読取専用。`
- `system_prompt`: `agents/dev-planner.md` の指示文をベースに、ツールマッピングを反映させたプロンプト。
- `enable_write_tools`: `false`

### dev-tester
- `name`: `dev-tester`
- `description`: `i-Willink品質検証（テスト/ビルド/リンター）実行サブエージェント。`
- `system_prompt`: `agents/dev-tester.md` の指示文をベースに、ツールマッピングを反映させたプロンプト。
- `enable_write_tools`: `true`（テスト実行に必要なコマンド実行権限）

### dev-reviewer
- `name`: `dev-reviewer`
- `description`: `i-Willink差分コードレビュー専門サブエージェント。読取専用。`
- `system_prompt`: `agents/dev-reviewer.md` の指示文をベースに、ツールマッピングを反映させたプロンプト。
- `enable_write_tools`: `false`
