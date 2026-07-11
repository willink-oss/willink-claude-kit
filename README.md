# willink-claude-kit

Claude Code / Codex / Antigravity 向け標準開発エージェント基盤。**Opus 4.7 前提**の Claude Code plugin を正本にし、各環境では adapter skill と同期チェックで同じ開発パフォーマンスを狙う。

> Status: **v1.0 — partial Go**（2 / 4 target stacks verified）。stable な surface（4 agents + 5 phase `/build`）はそのまま採用可。stack 別の検証状況は [docs/known-stack-coverage.md](docs/known-stack-coverage.md) を参照。

## 提供するもの

| 区分 | 内容 |
|---|---|
| **agents/** (4本) | `dev-explorer` / `dev-planner` / `dev-tester` / `dev-reviewer` — 公式 ガイドラインに沿って役割を厳選 |
| **skills/dev-standards** | スタック非依存の汎用標準（TS strict / Conventional Commits / OWASP） |
| **commands/build.md** | 5 phase 版 `/build` フロー（探索→計画→実装→並列検証→修正/コミット） |
| **commands/goal-loop.md** + **scripts/goal-loop\*.sh** | 組み込み `/goal` に「決定論 `--check` + 試行上限」の規律を足す停止プリミティブ。達成をモデルの自己申告でなく exit code で判定し N 回で必ず止める。雛形生成器 + `maker-checker-relay`（Generator↔Verifier=実装↔`dev-reviewer` レビューを分離して回す）付き。全て hermetic 自己テスト付き |
| **skills/maker-checker-relay** | 実装（Maker）と読取専用レビュー（Checker=`dev-reviewer`/`/review`/人）を分離し「test 緑 かつ 指摘 0」まで反復する goal-loop ラッパー |
| **commands/pulse.md** | 6 phase 版 `/pulse` フロー — プロジェクト現況を read-only で live 実測し状態表と次アクションを出す（文書=plan/live=state・自己申告禁止・probe 失敗は 0 でなく ❓）。`/build` と対の「測る」コマンド。`scripts/pulse-precheck.sh` が Verifier |
| **examples/hooks/** ・ **examples/git-hooks/** | 決定論ガードレールの雛形（コピーして wire）: 破壊コマンド gate・`.env`/鍵保護・test 失敗 advisory・compact スナップショット・tool ログ（Claude Code hooks）+ secret/サイズ/`.env` gate・BSD-grep+shellcheck lint（git pre-commit）。全て block+pass 自己テスト付き |
| **commit/CI 品質ゲート** (skills/ + scripts/) | 決定論ゲート 5 種: `coverage-floor-lock`（カバレッジ下限 + floor 引き下げ検出）・`token-codegen-gate`（生成物ドリフト＝再生成して差分検査）・`architecture-parity-gate`（config 宣言の依存方向 + 命名規則違反）・`commit-convention-gate`（prefix/空虚メッセージ/なぜ欠落）・`self-heal-ci`（赤 CI を緑まで自己修復・`goal-loop.sh` の試行上限付き）。全て hermetic 自己テスト付き・config 駆動は example 同梱 |
| **検証投票ゲート** (skills/ + scripts/) | 多エージェント検証の採否を決定論集約する合議ゲート 3 種: `adversarial-refute-vote`（1 主張を N 個の独立 refute 票にかけ過半反証で採用停止・`scripts/refute-vote.py`）・`judge-rubric-vote`（複数 judge の verdict を多数決 + 合意率閾値で規律化し hung は fail・`scripts/judge-vote.py`）・`fanout-verify-synth`（fan-out した claim 検証を集約し全 verified なら統合可・refuted があれば人へエスカレーション・`scripts/fanout-verify-synth.sh`）。いずれも自己申告禁止（"verified" ラベルだけでは採らず票/出典を要求）・票が取れない状態は「採用可」に倒さない・LLM 非依存の決定論集約。全て hermetic 自己テスト付き |
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

- **v0.1.x** — 社内プロダクト 2 本での並列検証（2 週間）
- **v0.2.x** — 効果測定後の調整
- **v1.0.0** — 全社プロジェクトへの展開完了

## 姉妹プロジェクト

- [**ai-coo-starter**](https://github.com/willink-oss/ai-coo-starter) — AI を COO にして一人会社を回す運営構造のスターターテンプレ。kit が「開発エージェント基盤」なら、ai-coo-starter は「会社運営の骨格」（承認境界・working rules・承認待ちを炙り出す standup・部署/routine テンプレ）。

## 関連ドキュメント

- [docs/adoption-guide.md](docs/adoption-guide.md) — 各リポへの導入手順
- [docs/codex-adoption-guide.md](docs/codex-adoption-guide.md) — Codex plugin 導入手順
- [docs/antigravity-adoption-guide.md](docs/antigravity-adoption-guide.md) — Antigravity 導入手順
- [docs/verification-protocol.md](docs/verification-protocol.md) — 検証指標と記録テンプレ
- [docs/stack-specific-notes.md](docs/stack-specific-notes.md) — Next.js / Flutter / WordPress 個別注意
- [docs/failure-modes.md](docs/failure-modes.md) — Early victory / Telephone game 等の対策
- [docs/hooks-guide.md](docs/hooks-guide.md) — hook の書き方・自己テスト・fail-open/closed・grep 移植性規約
- [docs/harness-profile.md](docs/harness-profile.md) — 決定論的ゲートの導入プロファイル（H1-H4 ラダー・CI required check・昇格運用）
- [docs/session-hygiene.md](docs/session-hygiene.md) — セッション衛生（new task=new session・/compact・Esc+Esc rewind）
- [docs/subagent-guidelines.md](docs/subagent-guidelines.md) — subagent 委譲の判断（委譲する/しない・型選択・hand-off）

## ライセンス

MIT — see [LICENSE](LICENSE)
