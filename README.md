# willink-claude-kit

Claude Code / Codex / Antigravity 向け標準開発エージェント基盤。Claude Code plugin を正本にし、各環境では adapter skill と同期チェックで同じ開発パフォーマンスを狙う（agents は `model: inherit` でセッションのモデルに追従）。

> Status: **stable surface** — 中核 surface（4 agents + 5 phase `/build`）は安定で、Claude Code / Codex / Antigravity の 3 環境に対応。そのまま採用可。target stack の検証は **2 / 4 verified**（残り 2 stack は install-only・Q3 dogfood 待ち）。現行バージョンは [CHANGELOG.md](CHANGELOG.md)、stack 別の検証状況は [docs/known-stack-coverage.md](docs/known-stack-coverage.md) を参照。

## 提供するもの

| 区分 | 内容 |
|---|---|
| **agents/** (4本) | `dev-explorer` / `dev-planner` / `dev-tester` / `dev-reviewer` — 公式 ガイドラインに沿って役割を厳選 |
| **skills/dev-standards** | スタック非依存の汎用標準（TS strict / Conventional Commits / OWASP） |
| **commands/build.md** | 5 phase 版 `/build` フロー（探索→計画→実装→並列検証→修正/コミット） |
| **skills/codex-build** | Claude Code の `/build` を Codex で実行する adapter skill |
| **skills/antigravity-build** | Claude Code の `/build` を Antigravity で実行する adapter skill |
| **.codex-plugin/plugin.json** | Codex plugin entrypoint。`.claude-plugin/plugin.json` の core metadata と同期 |
| **examples/project-standards-template/** | 各プロジェクトが固有のドメイン知識を `.claude/skills/project-standards/` で拡張する雛形 |

## 設計原則

1. **a handful of well-scoped agents** — 4 本に厳選（公式: agent flooding は automatic delegation の信頼性を下げる）
2. **Generator-Verifier 分離** — 実装はメイン、レビューは read-only subagent（telephone game 回避）
3. **Context-centric decomposition** — 並列化は独立した調査パスに限る
4. **Skills 二段 preload** — kit 提供 `dev-standards` + project 提供 `project-standards`（後者欠落でも warning のみ）
5. **Read-only / Write-allowed の二極化** — 全 agent で tools 明示

## 導入方法

### Claude Code

各リポジトリの `.claude/settings.json` に以下を追加:

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

バージョン pin 例: `"willink-claude-kit@iwillink": ["2.1.0"]`

> Claude Code 公式 `settings.json` schema は `enabledPlugins.<plugin>` の値として `boolean` または `array<string>` のみ受け付ける。バージョン pin は **array 形式**で書くこと（string `"0.1.1"` は schema validator に弾かれる）。

詳細は [docs/adoption-guide.md](docs/adoption-guide.md) を参照。

### Codex

Codex では repo root の `.codex-plugin/plugin.json` を plugin entrypoint として使う。`codex-build` skill が Claude Code の 5 phase flow を Codex に適応する。

詳細は [docs/codex-adoption-guide.md](docs/codex-adoption-guide.md) を参照。

### Antigravity

Antigravity では `skills/antigravity-build/SKILL.md` を adapter skill としてロードして使用します。

詳細は [docs/antigravity-adoption-guide.md](docs/antigravity-adoption-guide.md) を参照。

## ドメイン知識の拡張

各プロジェクトで `.claude/skills/project-standards/SKILL.md` を作成すると、kit の 4 agent と Codex / Antigravity の adapter はこれを参照する。雛形は [examples/project-standards-template/](examples/project-standards-template/) からコピー。

`dev-reviewer` の memory も `.claude/agent-memory/dev-reviewer/MEMORY.md` を Claude Code / Codex / Antigravity で共有する。プラットフォーム専用のコピーは作らない。

なお Claude Code の **plugin install** では、auto-write（`memory: project`）が plugin 名前空間付きの `.claude/agent-memory/willink-claude-kit-dev-reviewer/` に着地し、発火するのは `/build` 内のみ（standalone `/agents` では発火しない）。共有の正本は上記 path のまま — 着地先ごとの条件と統合手順は [docs/adoption-guide.md](docs/adoption-guide.md) §3.2 を参照。

## Claude → Codex / Antigravity 同期

Claude Code 側の canonical files を変更したら、Codex/Antigravity adapter の同期を確認する:

```bash
python3 scripts/check_sync.py --check
```

意図した正本変更に各プラットフォーム側も追従済みなら hash manifest を更新する:

```bash
python3 scripts/check_sync.py --update
```

## ロードマップ

- **〜v2.1.0（済）** — Claude Code 正本 + Codex / Antigravity adapter による 3 環境同期基盤、release / docs 整合ガード、回帰テストスイートを整備。
- **次の minor** — install-only の 2 stack（Flutter+Supabase / Next.js+Stripe）を Q3 dogfood で ✅ verified へ promotion（基準は [docs/known-stack-coverage.md](docs/known-stack-coverage.md) の Promotion plan）。
- **継続** — v1.1 繰り越し指標（dev-reviewer MEMORY ≥ 5 / context 1.3x）の判定と、回帰スイートの自走改善ループ（crew `loop-test-cycle`）によるカバレッジの単調増加。

リリース履歴は [CHANGELOG.md](CHANGELOG.md) を参照。

## 関連ドキュメント

- [docs/adoption-guide.md](docs/adoption-guide.md) — 各リポへの導入手順
- [docs/codex-adoption-guide.md](docs/codex-adoption-guide.md) — Codex plugin 導入手順
- [docs/antigravity-adoption-guide.md](docs/antigravity-adoption-guide.md) — Antigravity 導入手順
- [docs/verification-protocol.md](docs/verification-protocol.md) — 検証指標と記録テンプレ
- [docs/stack-specific-notes.md](docs/stack-specific-notes.md) — Next.js / Flutter / WordPress 個別注意
- [docs/failure-modes.md](docs/failure-modes.md) — Early victory / Telephone game 等の対策

## ライセンス

MIT — see [LICENSE](LICENSE)
