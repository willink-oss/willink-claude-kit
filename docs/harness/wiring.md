# 配線 — 「観測」から「止まる」へ

`install.sh` は skill とエンジンを置くだけです。**何も止まりません。**
止まるようにするのは、ここに書いた手順を人間が実行したときです。

配置元の commit / branch / UTC 日時は `.claude/willink-kit/SOURCE.md` に残ります（`--dry-run` では書きません・git 情報が取れなければ「不明」）。再インストール前にそこを見れば、何が変わるかを `git diff <commit>..HEAD` で確かめられます。

再インストールすると、**前回配布した内容のままの skill は上書きして更新**します。手で直した skill と consumer 独自の同名 skill は据え置き、「手で直された skill N/M 本」と分母つきで報告します（判定は正本の git 履歴との比較。正本が git でなければ「判定不能 N/M 本」と言い、0 件とは言いません）。engine を実行すると出来る `__pycache__/` `*.pyc` は `.claude/willink-kit/.gitignore` で無視されます（install.sh が置き、既存の行は消しません）。

段階を踏むことを強く勧めます。いきなり全部を fail-closed にすると、
最初の 1 日で作業が回らなくなり、結局ゲートを外すことになります。
**一度外したゲートは、二度と戻りません。**

---

## 段階 1 — 観測だけ（推奨: 最初の 2 週間）

何も止めず、数字だけ取ります。

```bash
cd /path/to/your/repo
E=.claude/willink-kit/engines

python3 $E/harness-lint.py skill-desc      # skill 定義の不備
python3 $E/harness-lint.py rule-dedup      # ルールの重複
python3 $E/govern.py kpi                   # ハーネス KPI を 1 行の台帳で
python3 $E/agentlog.py --help              # セッション観測系
```

この段階で見るのは**件数ではなく分母**です。
「0 件」が出たら、それが「走査して 0 件」なのか
「そもそも走査していない」のかを必ず確認してください。
全エンジンは分母を出します。出ていなければ、それは対象が無いということです。

**2 週間ぶんの数字が溜まってから、次に進みます。**
初日の数字は、閾値を決める材料になりません。

---

## 段階 2 — CI で落とす（可逆・推奨）

CI で落とすのは可逆です。ワークフローを 1 行消せば元に戻ります。
まずここまで。

```yaml
# .github/workflows/harness.yml
name: harness
on: [pull_request]
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python3 .claude/willink-kit/engines/harness-lint.py skill-desc --check
      - run: python3 .claude/willink-kit/engines/govern.py adr-lint --json
```

`--check` を付けたときだけ「対象あり かつ 違反 > 0」で exit 1 になります。
**対象が無い場合は `--check` でも exit 0** です（欠如は失敗ではない）。

### required status check にするかどうか

これは**ブランチ保護の変更**なので、権限を持つ人の判断です。
`ci-required-check-installer` は現状を読み取り専用で監査し、
適用コマンドの例を印字するだけで、**自分では適用しません**。

```bash
bash $E/required-check-audit.sh
```

---

## 段階 3 — フックで止める（不可逆に近い・慎重に）

ローカルのフックは、作業中に止まります。ここから体感が変わります。

**導入前に必ず: block ケースと pass ケースの両方をテストする。**

block ケースだけ書くと、**全部 block する実装がテストに通ります**。
実際に起きた事故です（[`incidents.md` A-5](incidents.md)）。

`gate-forge` は、この 2 ケースを対で含む scaffold を生成します。

```bash
bash $E/gate-forge.sh --describe "本番 DB への破壊的操作を止めたい" --out /tmp/gate-draft
# 生成物を読み、テストを実行し、内容を理解してから配線する
```

### fail-closed / fail-open の別

| 種類 | イベント | 失敗時 | 理由 |
|---|---|---|---|
| security | `PreToolUse` | **exit 2（fail-closed）** | パースに失敗したら通してはいけない |
| notification | `PostToolUse` / `UserPromptSubmit` / `SessionEnd` | exit 0（fail-open） | 通知の失敗で作業を止めない |

**fail-closed なフックが依存する CLI は単一障害点です。**
1 つのコマンドが欠けただけで全操作が止まった実例があります。
依存は 2 系統用意するか、無い場合の挙動を明示的に設計してください。

そして逆方向の罠が、より危険です。

> **依存ツールが無いとき skip するゲートは、黙って緑を返します。**
> fail-closed は「止まって気づく」のに対し、skip は誰も気づきません。
> skip 理由を「本当に未導入」と「導入済だが解決できない」に分け、
> **後者は異常として鳴らしてください**（[`incidents.md` A-1](incidents.md)）。

### 破壊コマンドの現状監査

```bash
bash $E/destructive-audit.sh --hook /path/to/your/pre-bash-safety.sh
```

塞がれていないコマンド集合を列挙します。**フックは改変しません。**

---

## 環境変数

| 変数 | 用途 | 既定 |
|---|---|---|
| `PH_TARGET_ROOT` | 検査対象リポジトリ | `git rev-parse --show-toplevel` → cwd |
| `PH_MEMORY_DIR` | Claude Code の project memory | cwd から導出 |
| `PH_KNOWLEDGE_DIR` | 知識ベースのディレクトリ | `assets/knowledge` |
| `PH_KNOWLEDGE_INDEX` | 知識ベースの索引 | `assets/knowledge-base.md` |
| `PH_ROUTINE_REGISTRY` | 定期ルーチンの登録簿 | `ops/routines/registry.md` |
| `PH_UPSTREAM_KIT_DIR` | drift 比較の上流 | `~/GitHub/willink-claude-kit` |

CLI オプション（`--root` `--rules-dir` `--skills-dir` `--memory-dir` `--knowledge-dir`
`--adr-dir` `--hooks-dir`）は、環境変数より優先されます。

---

## やってはいけないこと

**ゲートが止めたときに、ゲートの側を緩めて通す。**

これを一度でも許すと、ゲートは以後ただのログになります。
止まったら、止まった理由を解決するか、人間の判断に上げてください。
どちらもできないなら、そのゲートは最初から要りません
（[`principles.md` P3](principles.md)）。

---

## consumer の配線は plugin 経路（推奨・2026-09-17）

**正本を更新しても consumer を触らない**形。hook / skill / engine は kit（plugin の cache）から、fixture は
CI が正本を checkout して当てる。consumer に置くのは次の 3 ファイルと settings の数行・CI の 1 段だけ
（雛形は `core/packaging/consumer/`・`scripts/wire-consumer.sh --target <repo> [--migrate]` が置く）。

| consumer に置く | 何 |
|---|---|
| `.claude/settings.json` | `extraKnownMarketplaces.iwillink` + `enabledPlugins["willink-claude-kit@iwillink"]=true`。**inline の hook 登録は書かない**（kit の `hooks/hooks.json` と二重になる） |
| `scripts/harness-check.sh` | 薄い wrapper。中身は kit の cache か `HARNESS_ROOT` の `consumer-check.sh` |
| `.githooks/pre-commit` | kit の pre-commit 3 本を cache から呼ぶ resolver（cache が無い端末は ⚠️ で通し、止める場所は CI） |
| `.claude/harness-targets.json` | fixture をどこに当てるか（`{"fixtures": {"not-wired-values": ["lib"], ...}}`） |
| CI の 1 job | 正本を `HARNESS_READ_PAT`（org secret・read のみ）で `.harness` に checkout → `HARNESS_ROOT=.harness bash scripts/harness-check.sh` |

`consumer-check.sh` は 6 項目（コピー方式が残っていない／kit の hooks.json と block・pass の probe／配線経路 1 本／
fixture の self-test と実適用／git hook／課題台帳）を分母つきで見て、exit 0 / 1 / 2（測れていない）を返す。
`install.sh`（コピー方式）は fixture を in-repo で持ちたい consumer のために残すが、新規は plugin 経路にする。

## plugin として入れた場合の hook（2026-09-10 実測）

Claude Code の plugin は **`hooks/hooks.json`** を読んで hook を登録します。
`plugin.json` に `hooks` キーを足すのではありません。**`hooks/*.sh` を同梱しただけでは
1 本も登録されません**（v2.6.0 はこの状態で 14 本を配り、実効 0 本でした）。

本 kit は `hooks/hooks.json` で **10 本**を登録します。

| イベント | matcher | hook | 既定 |
|---|---|---|---|
| PreToolUse | `Bash` | `pre-bash-safety.sh` | **止める** |
| PreToolUse | `Write\|Edit` | `pre-file-protect.sh` | **止める** |
| PreToolUse | `Write` | `pre-write-collision.sh` | **止める** |
| PostToolUse | `Bash` | `post-commit-verify.sh` | fail-open |
| PostToolUse | `Write\|Edit` | `post-file-eval.sh` | fail-open |
| PostToolUse | — | `post-tool-log.sh` | fail-open |
| UserPromptSubmit | — | `pre-status-verify-guard.sh` | fail-open |
| UserPromptSubmit | — | `review-gate.sh` | 設定が無ければ通す |
| PreCompact | — | `pre-compact-snapshot.sh` | fail-open |
| InstructionsLoaded | — | `instructions-loaded-log.sh` | fail-open（このイベントは公式一覧に載っていないと指摘されることがあるが、**発火する**: 2026-09-17 に Claude Code 2.1.273 で `claude -p` 1 回 → SessionStart → InstructionsLoaded → UserPromptSubmit の順に 1 回ずつ記録された） |

**同梱しているが登録しない 4 本**: `pre-commit-quality.sh` / `pre-commit-shell-lint.sh` /
`pre-commit-silent-zero.sh` は **git の pre-commit hook** で、Claude Code のイベントには
載りません（`.git/hooks/pre-commit` から呼びます）。`_advisory-log.sh` は共有ライブラリです。

### 経路は 1 本（plugin か install.sh か・2026-09-17 実測）

Core の hook は **plugin 経路**（kit の `hooks/hooks.json`・2.7.0 以降・cache はリポの外）と
**install.sh 経路**（`.claude/willink-kit/hooks/*.sh` を置いて settings.json に人手で inline 登録）の
2 つで届きます。Claude Code は**両方をそのまま両方**登録します（合成 plugin で 1 prompt を送ると
UserPromptSubmit が plugin 側と inline 側で 1 回ずつ = 2 回走った。dedup は無い）。
kit 2.7.0 の hooks.json 10 本と install.sh 経路の inline 10 本は同名で **10/10 重なる**ので、
kit を有効にしたまま inline 登録すると、**cache の kit が hooks.json を持つ版に更新された瞬間に
10 本が 2 回ずつ走ります**（止める 3 本は 2 回止め、ログは 2 重、review-gate は毎 prompt 2 回）。
配線した日には見えず、`/plugin marketplace update` 1 回で起きます。

- 実測: `python3 .claude/willink-kit/engines/hook-wiring-check.py`（exit 0 = 経路 1 本 / 1 = 二重あり /
  2 = 測れていない: settings.json が無い・settings / registry / hooks.json のどれかが読めない）。settings は
  Claude Code と同じ **user（`~/.claude/settings.json`）→ project → local** の 3 層を重ねて読みます
  （`claude plugin install` の既定 scope は user なので、project の settings.json だけ見ると「未宣言」に見える）。
  `install.sh` は完了時（`--dry-run` でも）の案内でこれを advisory として出します
- **検出点は開発機**: plugin の cache（`~/.claude/plugins/cache/`）は開発機にしかなく、CI には無いので
  CI で回しても ⚠️（潜在）止まりで ❌ にはなりません。開発機で毎 prompt 走る `review-gates.tsv`
  （harness-check）や pre-commit に載せてください
- どちらか 1 本に: **(A)** inline を外して plugin に任せる（in-repo の hook 修正は kit の次の版まで
  届かない）／ **(B)** `enabledPlugins` の kit を false にして install.sh 経路だけにする（kit の skill も消える）
- git の pre-commit hook 3 本はどちらの経路でも in-repo（`.git/hooks` / husky）なので二重にはならない

### 止める 3 本の既定

- `pre-bash-safety.sh` — 直 push を許すリポは `HARNESS_DIRECT_PUSH_REPOS`（カンマ区切り）。
  **既定は空 = 常に PR を要求**します。単独リポで直 push したい場合は設定してください。
- `pre-file-protect.sh` / `pre-write-collision.sh` — 保護対象への上書きと、
  同一ファイルへの衝突書き込みを止めます。

### パスの前提

hook は **`CLAUDE_PROJECT_DIR`**（無ければ `git rev-parse --show-toplevel`、それも無ければ `pwd`）を
プロジェクトのルートとして使います。plugin 配下では `$0` は **plugin のキャッシュ**を指すため、
`dirname "$0"` から遡ってリポジトリを推測すると、設定は永久に見つからず、
ログとスナップショットが全プロジェクトで共有されます。`scripts/test-plugin-hooks.sh` が
この書き方の再発を止めます。
