# Codex 導入手順

この repo は Claude Code plugin を正本としつつ、Codex plugin としても同じ i-Willink 開発標準を提供する。

## 1. Codex plugin を有効化

Codex 側では repo root の `.codex-plugin/plugin.json` を plugin entrypoint として使う。

この plugin が読み込まれると、次の skill が使える:

- `dev-standards` — i-Willink 共通開発標準
- `codex-build` — Claude Code の `/build` を Codex 用に適応した 5 phase flow

`.agents/plugins/marketplace.json` はこの repo では作成しない。配布・有効化の入口は `.codex-plugin/plugin.json` に統一する。

## 2. project-standards は Claude path を共有する

downstream repo では Claude Code と同じ場所に project-specific skill を置く:

```bash
mkdir -p .claude/skills/project-standards
cp path/to/willink-claude-kit/examples/project-standards-template/SKILL.md \
   .claude/skills/project-standards/SKILL.md
```

Codex 専用の project-standards copy は作らない。Codex の `codex-build` skill は `.claude/skills/project-standards/SKILL.md` を読む。

## 3. dev-reviewer memory も共有する

Claude Code と Codex の review pattern を同じ memory に蓄積する:

```bash
mkdir -p .claude/agent-memory/dev-reviewer
touch .claude/agent-memory/dev-reviewer/MEMORY.md
git add .claude/agent-memory/dev-reviewer/MEMORY.md
```

この memory は version control 対象にする。local-only にしたい個人メモは `.claude/agent-memory-local/` を使う。

## 4. Codex での使い方

Codex にタスクを渡すときは、必要に応じて次のように明示する:

```text
Use codex-build for this task.
```

`codex-build` は Claude Code の 5 phase flow を維持する。ただし Codex では、subagents はユーザーが明示的に依頼した場合だけ使う。明示がなければ main Codex session が探索・計画・実装・検証・修正を通して担当する。

## 5. Claude 変更との同期確認

Claude Code 側の canonical files を変更したら、Codex adapter の同期を確認する:

```bash
python3 scripts/check_codex_sync.py --check
```

Claude 側の正本を意図して変更し、Codex adapter も追従済みなら manifest hash を更新する:

```bash
python3 scripts/check_codex_sync.py --update
python3 scripts/check_codex_sync.py --check
```

CI でも `.github/workflows/codex-sync.yml` が同じ check を実行する。

## 6. セッションへの反映

Codex plugin は既存セッションへ hot-load されない。marketplace / plugin cache に新しい release tag が反映された後、新しい Codex セッションで `codex-build` skill が available skills に出ることを確認する。
