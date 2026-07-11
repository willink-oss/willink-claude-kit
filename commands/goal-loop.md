---
description: 組み込み /goal に「決定論的な達成判定 + 試行上限」の規律を上乗せするヘルパー — 達成はモデルの自己申告でなく --check の exit code で判定し、N 回で必ず止める。
allowed-tools: Read, Glob, Grep, Bash, Edit, Write
---

# /goal-loop — disciplined goal-directed loops

ゴール指向ループを、**決定論的な達成判定**と**試行上限**の規律付きで回す。

> ⚠️ **`/goal` は Claude Code の組み込みコマンド**（`/goal [condition|clear]` — 条件を満たすまでターンを跨いで作業継続）。本コマンド `/goal-loop` はそれを**置き換えません**。組み込み `/goal` は「達成判定がモデル自身」ですが、`/goal-loop` はそれを **決定論コマンド `--check` の exit code** と **試行上限 `--max`** で締めるための薄いヘルパーです（自己申告で「達成した」と書かせない）。

## 使い分け

- **組み込み `/goal <condition>`** … ループ機構本体（ターンを跨いだ継続）。停止は `/goal clear`
- **`/goal-loop`（本コマンド）** … その condition を**決定論コマンドで判定**し、**N 回で必ず止める**規律を足したい時。プリミティブ = `scripts/goal-loop.sh`

## 入力

`$ARGUMENTS` に「① ゴール（1 文）② `--check` にする決定論コマンド（exit 0=達成）③ 最大試行回数」を含める。曖昧なら着手前に確定する。
例: `mailer.ts のカバレッジ 90% / check: npm run test:coverage / max: 4`

## 手順（stop primitive = `scripts/goal-loop.sh`）

停止プリミティブ `scripts/goal-loop.sh` の exit code: **0=✅GOAL MET / 1=🔁CONTINUE / 2=🛑CAP REACHED**。

1. **ゴールと `--check` を確定**（`--check` は決定論的であること — テスト緑・カバレッジ閾値・lint=0・スコア閾値。主観・自己申告は不可）
2. 初期化: `bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/goal-loop.sh" --reset --state <state>`
3. **ループ**（各周の冒頭で呼ぶ）:
   ```
   bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/goal-loop.sh" --goal "<説明>" --check "<cmd>" --max <N> --state <state>
   ```
   - exit 0（GOAL MET）→ 達成。終了・成果を commit / PR
   - exit 1（CONTINUE）→ `--check` を **1 歩だけ**前進させる作業をして再度 goal-loop
   - exit 2（CAP REACHED）→ 上限到達。**停止して人へエスカレーション**（未達ゴールを blocker 追跡先に 1 行記録）
4. 各周は最小限に（一発達成を狙わない）
5. 完了後、達成値・試行回数を報告し、必要なら **その `--check` を CI/フックに固定**して回帰させる（到達点を floor に）

## ループ雛形の生成（任意）

新しい指標ごとに呼び出しを手書きせず、配線済みの実行可能ループを生成できる:
```
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/goal-loop-template.sh" \
    --name <slug> --check "<決定論コマンド>" [--goal "<説明>"] [--max <N>] --out scripts/loop-<slug>.sh
```
生成物の `CONTINUE`（exit 1）分岐に「`--check` を 1 歩前進させる作業」を実装する（TODO 箇所）。

## Maker-Checker relay（実装とレビューを分離したい時）

「実装した本人が自己レビューして『直った』と宣言する」バイアスを排除したい時は、
Generator（Maker=実装）と Verifier（Checker=読取専用レビュー・**kit の `dev-reviewer` agent** / `/review` / 人）を
分離して回す `maker-checker-relay` を使う（`test 緑` かつ `Checker 指摘 0` で初めて完了）。
→ `skills/maker-checker-relay/`

## 使いどころ / 使わないところ

- ✅ 決定論的ゴールがあり反復で詰められる（カバレッジ・バグゼロ・パフォーマンス閾値・スコア）
- ❌ 停止条件が曖昧なオープンエンド探索 / 単一ターンで終わる / 主観評価が必要（→ `/build` や AI レビュー）

## 関連

- 組み込み `/goal`: https://code.claude.com/docs/en/commands.md
- primitive: `scripts/goal-loop.sh` / generator: `scripts/goal-loop-template.sh`
- relay: `skills/maker-checker-relay/` + `scripts/maker-checker-relay.sh`
- `docs/harness-profile.md` — 決定論ゲートの ladder（goal-loop は「自己申告禁止」の停止層）
- 自己テスト: `scripts/test/test_goal_loop.sh`
