# 各リポジトリへの導入手順

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

バージョン pin する場合: `"willink-claude-kit@iwillink": "0.1.0"`

> Phase B 検証中は **必ず pin** する（v0.x は破壊的変更ありうるため）

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

---

## トラブルシュート

| 症状 | 原因 | 対処 |
|---|---|---|
| `/agents` に dev-* が出ない | plugin install 失敗 | `claude --debug` で marketplace fetch ログを確認 |
| project-standards が読まれない | パスが違う or skill 名不一致 | `.claude/skills/project-standards/SKILL.md` の name フィールドが `project-standards` になっているか |
| dev-reviewer が memory を読まない | memory directory 未作成 | 上記 step 3 を実行 |
| Plugin が `/build` を上書きしない | 既存 `.claude/commands/build.md` が priority 高 | 既存を退避するか、別名で呼ぶ |
