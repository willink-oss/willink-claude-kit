# Claude Code 導入手順

このページは Claude Code plugin の導入手順。Codex 側も使う場合は [codex-adoption-guide.md](codex-adoption-guide.md) を併用する。`project-standards` と `dev-reviewer` memory は Claude Code / Codex で同じ `.claude/` path を共有する。

## 1. Plugin marketplace を登録

各リポジトリの `.claude/settings.json` に追加:

```json
{
  "extraKnownMarketplaces": {
    "iwillink": {
      "source": {"source": "github", "repo": "willink-oss/willink-claude-kit"}
    }
  },
  "enabledPlugins": {
    "willink-claude-kit@iwillink": true
  }
}
```

バージョン pin する場合: `"willink-claude-kit@iwillink": ["2.0.0"]`

> Phase B 検証中は **必ず pin** する（v0.x は破壊的変更ありうるため）

> ⚠️ Claude Code 公式 `settings.json` schema は `enabledPlugins.<plugin>` の値として `boolean` または `array<string>` のみ受け付ける。`"0.1.1"` のような **string は schema 違反**で validator に弾かれる（`$schema` を宣言したリポでは特に）。array 形式 `["0.1.1"]` を使うこと。

## 2. project-standards を作成

`.claude/skills/project-standards/SKILL.md` を作成:

```bash
# kit リポジトリから雛形をコピー
cp -r path/to/willink-claude-kit/examples/project-standards-template/SKILL.md \
      .claude/skills/project-standards/SKILL.md
```

`<PROJECT_NAME>` 等のプレースホルダを埋める。最初は薄くて OK — 開発しながら育てる。

## 3. dev-reviewer の memory directory を準備

```bash
mkdir -p .claude/agent-memory/dev-reviewer
touch .claude/agent-memory/dev-reviewer/MEMORY.md
git add .claude/agent-memory/dev-reviewer/MEMORY.md
git commit -m "chore: initialize dev-reviewer memory"
```

`.gitignore` に **追加しない**（agent の学習を team / 自分自身と共有するため version control 対象）。

local-only にしたい場合は `.claude/agent-memory-local/dev-reviewer/` を使い、`.gitignore` に追加。

### 3.1 既存 `.gitignore` に `.claude/agent-memory/` がある場合（既存リッチハーネス repo 向け）

既存 repo に `.claude/agent-memory/` の除外が既にあると、kit が要求する MEMORY.md を git add しても **silent に除外**されて team 共有が機能しない。次のいずれかで解消すること:

- 除外を local 用に書き換え: `.gitignore` の `.claude/agent-memory/` を `.claude/agent-memory-local/` に変更（推奨）
- 個人学習用 memory を分けたい場合のみ `.claude/agent-memory-local/dev-reviewer/` を作って .gitignore で除外

検証コマンド:

```bash
git check-ignore -v .claude/agent-memory/dev-reviewer/MEMORY.md
# 何も出なければ OK（除外されていない）
```

### 3.2 memory path と auto-write 条件

memory は「どこに書かれるか」が install 形態で変わる。**正本（共有 path）は常に
`.claude/agent-memory/dev-reviewer/MEMORY.md`**（全プラットフォーム共有・version control 対象）。

| 文脈 | auto-write | 実際の書き込み先 |
|---|---|---|
| Claude Code plugin install + `/build` Phase 4 | ✅ 発火（v1.0 検証で 3 entries 実測） | `.claude/agent-memory/willink-claude-kit-dev-reviewer/`（plugin 名前空間付き） |
| Claude Code plugin install + standalone `/agents` | ❌ 発火しない（既知制約 = harness 挙動・kit 側では制御不可） | — |
| `agents/` を手動コピーした install | `/build` 内のみ（期待値・未実測） | `.claude/agent-memory/dev-reviewer/`（期待値・未実測） |
| Codex（`codex-build`） | ❌ auto-write 機構なし | 手動更新のみ |
| Antigravity（`antigravity-build`） | ❌ auto-write 機構なし | 手動更新のみ |

**plugin install 時の consolidation 手順**（auto-write は名前空間付き directory に溜まるため、
週次 or リリース前に正本へ統合する）:

1. `.claude/agent-memory/willink-claude-kit-dev-reviewer/MEMORY.md` を開く
2. 恒久パターン（anti-pattern・規約訂正・アーキテクチャ知見）だけを
   `.claude/agent-memory/dev-reviewer/MEMORY.md` へ転記
3. 転記済みエントリは名前空間側から削除して肥大を防ぐ

standalone `/agents` 呼び出しや Codex / Antigravity セッションで得たパターンは
auto-write されないため、正本 MEMORY.md へ**手動追記**する。

## 4. 既存 /build コマンドとの衝突回避

リポジトリに既に `.claude/commands/build.md` がある場合、Plugin 版が **上書きされない**（plugin priority は 5 = 最低）。

既存 build を使い続けたい場合: そのまま OK。
Plugin 版を使いたい場合: 既存 `.claude/commands/build.md` を rename or 削除。

## 5. 動作確認

```bash
# subagent が認識されているか
claude
> /agents
# dev-explorer / dev-planner / dev-tester / dev-reviewer が一覧に出ること

# build コマンドが認識されているか
> /build
# 5 phase のヘルプが出ること
```

## 6. 最初のタスクで /build を実行

小さな bug fix or docs 修正で慣らす。Phase 1/2 を skip するケースの方が多いことを確認。

詳細な検証プロトコル → [verification-protocol.md](verification-protocol.md)

## 7. Codex と併用する場合

Codex 側は repo root の `.codex-plugin/plugin.json` を entrypoint として使う。追加で `.codex/skills/project-standards/` のような copy は作らない。

Codex の `codex-build` skill は次をそのまま読む:

- `.claude/skills/project-standards/SKILL.md`
- `.claude/agent-memory/dev-reviewer/MEMORY.md`

Claude Code 側の `/build` や agent prompt を変更したら、Codex adapter の drift check を実行する:

```bash
python3 scripts/check_codex_sync.py --check
```

---

## トラブルシュート

| 症状 | 原因 | 対処 |
|---|---|---|
| `/agents` に dev-* が出ない | plugin install 失敗 | `claude --debug` で marketplace fetch ログを確認 |
| project-standards が読まれない | パスが違う or skill 名不一致 | `.claude/skills/project-standards/SKILL.md` の name フィールドが `project-standards` になっているか |
| dev-reviewer が memory を読まない | memory directory 未作成 | 上記 step 3 を実行 |
| Plugin が `/build` を上書きしない | 既存 `.claude/commands/build.md` が priority 高 | 既存を退避するか、別名で呼ぶ |
