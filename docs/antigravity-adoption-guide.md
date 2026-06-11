# Antigravity 導入手順

本ガイドは、`willink-claude-kit`（開発エージェント基盤）の思想と5フェーズ開発フローを、**Antigravity**（Google DeepMind開発の自律型コーディングエージェント）の環境に導入し、動作させるための手順書です。

---

## 1. 導入要件

- Antigravity 動作環境
- プロジェクト内に `willink-claude-kit` がサブモジュールまたはプラグインとしてクローンされていること
  - 推奨配置: `plugins/willink-claude-kit/` または `skills/` 直下に本キットの `skills/` を配置

---

## 2. 導入手順

### Step 1: スキルのロード
Antigravity に以下のスキルを認識させます。
1. **共通標準**: `skills/dev-standards/SKILL.md`
2. **Antigravity用アダプター**: `skills/antigravity-build/SKILL.md`

ロード時には、Antigravity上で `view_file` を用いて [skills/antigravity-build/SKILL.md](file:///Users/yutaroshirai/GitHub/willink-claude-kit/skills/antigravity-build/SKILL.md) を読み込ませることで、エージェントにプロトコルをインプットします。

### Step 2: プロジェクト固有規約の配置 (任意)
プロジェクト固有の規約がある場合、以下のパスに配置します。
- `.claude/skills/project-standards/SKILL.md`
- 雛形は `examples/project-standards-template/` を参照。

### Step 3: サブエージェントの定義
Antigravity の `define_subagent` ツールを使用して、以下の4つのサブエージェントを定義します。

各エージェントのシステムプロンプトを設定する際は、本リポジトリの `agents/` 配下のファイル（ `dev-explorer.md`, `dev-planner.md`, `dev-tester.md`, `dev-reviewer.md` ）の内容を読み込み、プロンプトの入力として渡してください。

| エージェント名 | write_tools 権限 | 役割 |
| :--- | :---: | :--- |
| `dev-explorer` | `false` (読取専用) | コードベースの調査、影響範囲の特定。 |
| `dev-planner` | `false` (読取専用) | 実装計画案の作成。 |
| `dev-tester` | `true` (書き込み/実行可) | `run_command` を用いたテスト、ビルド、型チェック、リンターの実行。 |
| `dev-reviewer` | `false` (読取専用) | 実装差分のコードレビュー、プロジェクトメモリの更新。 |

---

## 3. 5フェーズフローの遂行方法 (Antigravity版)

Antigravity でタスクを開始した場合、以下の流れで進めます。

```
[メイン Antigravity] タスクの受付
  │
  ├── (影響範囲が3軸以上独立している場合)
  │    └── [dev-explorer] を `define_subagent` → `invoke_subagent` で並行起動して調査
  │
  ├── [dev-planner] に計画ドラフトを作らせる (変更 > 50行 or 複数ファイルの場合)
  │
  ├── [メイン Antigravity] が `implementation_plan.md` を整理し、ユーザーの承認を得る
  │
  ├── [メイン Antigravity] が `task.md` を作成して実装 (Phase 3)
  │
  ├── [dev-tester] & [dev-reviewer] を定義・起動して並行検証 (Phase 4)
  │    ├── tester: テスト・ビルド・型チェック・リンターをフル実行
  │    └── reviewer: 差分のレビュー、MEMORY.md への記憶蓄積
  │
  ├── [メイン Antigravity] 指摘内容の修正、最大2ループ再検証 (Phase 5)
  │
  └── [メイン Antigravity] `walkthrough.md` を作成して検証記録を提示、Conventional Commit
```

### 注意点：
- **実装の委譲禁止**: 実装をサブエージェントに行わせてはいけません。必ずメインの Antigravity が行います。
- **早期勝利の抑止**: `dev-tester` の実行時には `--bail` などのフラグを取り除き、常にフルスイートでテストを実行してください。
- **メモリの活用**: レビュアーのメモリファイル `.claude/agent-memory/dev-reviewer/MEMORY.md` に蓄積された知見を、毎回レビュー実行前に読み込ませるようにしてください。
