# willink-claude-kit

Claude Code 向け標準開発エージェント基盤。**Opus 4.7 前提**で、サブエージェント・Skills・Plugin の公式機能をフル活用してコンテキスト管理とタスク品質を底上げする。

> Status: **v0.x — unstable**。社内プロダクトでの検証フェーズ。仕様変更あり。

## 提供するもの

| 区分 | 内容 |
|---|---|
| **agents/** (4本) | `dev-explorer` / `dev-planner` / `dev-tester` / `dev-reviewer` — 公式 ガイドラインに沿って役割を厳選 |
| **skills/dev-standards** | スタック非依存の汎用標準（TS strict / Conventional Commits / OWASP） |
| **commands/build.md** | 5 phase 版 `/build` フロー（探索→計画→実装→並列検証→修正/コミット） |
| **examples/project-standards-template/** | 各プロジェクトが固有のドメイン知識を `.claude/skills/project-standards/` で拡張する雛形 |

## 設計原則

1. **a handful of well-scoped agents** — 4 本に厳選（公式: agent flooding は automatic delegation の信頼性を下げる）
2. **Generator-Verifier 分離** — 実装はメイン、レビューは read-only subagent（telephone game 回避）
3. **Context-centric decomposition** — 並列化は独立した調査パスに限る
4. **Skills 二段 preload** — kit 提供 `dev-standards` + project 提供 `project-standards`（後者欠落でも warning のみ）
5. **Read-only / Write-allowed の二極化** — 全 agent で tools 明示

## 導入方法

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

バージョン pin 例: `"willink-claude-kit@iwillink": ["0.1.0"]`

> Claude Code 公式 `settings.json` schema は `enabledPlugins.<plugin>` の値として `boolean` または `array<string>` のみ受け付ける。バージョン pin は **array 形式**で書くこと（string `"0.1.0"` は schema validator に弾かれる）。

詳細は [docs/adoption-guide.md](docs/adoption-guide.md) を参照。

## ドメイン知識の拡張

各プロジェクトで `.claude/skills/project-standards/SKILL.md` を作成すると、kit の 4 agent はこれを自動 preload する。雛形は [examples/project-standards-template/](examples/project-standards-template/) からコピー。

## ロードマップ

- **v0.1.x** — 社内プロダクト 2 本での並列検証（2 週間）
- **v0.2.x** — 効果測定後の調整
- **v1.0.0** — 全社プロジェクトへの展開完了

## 関連ドキュメント

- [docs/adoption-guide.md](docs/adoption-guide.md) — 各リポへの導入手順
- [docs/verification-protocol.md](docs/verification-protocol.md) — 検証指標と記録テンプレ
- [docs/stack-specific-notes.md](docs/stack-specific-notes.md) — Next.js / Flutter / WordPress 個別注意
- [docs/failure-modes.md](docs/failure-modes.md) — Early victory / Telephone game 等の対策

## ライセンス

MIT — see [LICENSE](LICENSE)
