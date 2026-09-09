---
name: gate-forge
description: 再発したミスの記述から fail-closed PreToolUse hook の scaffold（+ test-hooks の block/pass ケース雛形）を staging ディレクトリに生成する（既存 hooks/settings は変更しない・配線は人手）。トリガー語彙: gate forge, ガード生成, fail-closed hook 雛形, フック scaffold, ミスをガード化, hook ジェネレータ, gate-forge, 再発防止フック
allowed-tools: Bash, Read
---

# gate-forge（D01・fail-closed hook scaffold ジェネレータ）

> 「文書ベースの状態推定」「破壊コマンド」など `common-mistakes.md` に積んだ再発ミスを、
> 注意書きでなく**機械的なゲート**で止めたいとき、house 既存の fail-closed hook
> （`pre-file-protect.sh` / `pre-bash-safety.sh`）と同形の PreToolUse hook scaffold と、
> その block/pass を検証する test-hooks ケース雛形を **staging ディレクトリに生成**する。
> 手書きで stdin JSON parse / fail-closed(exit 2) / BSD grep 互換を組むと取りこぼしやすいので、
> 生成 + 決定論検証を機械化する（原則 P1 決定論ゲート）。

## ⚠️ 承認レベル（原則 P3）— 適用は人手・L3 は 責任者 承認

- **この skill / `scripts/gate-forge.sh` は生成のみ**。既存の `.claude/hooks/*` ・
  `.claude/settings.json` ・ `.githooks/` を**一切変更しない**（staging へ新規ファイルを出すだけ）。
- **fail-closed hook の実配線は Level 3**（self-lockout: 壊れた hook は全 tool 呼び出しを止めうる）。
  staging → `.claude/hooks/` 配置・`test-hooks.sh` へのケース移植・settings.json 登録は
  **責任者 事前承認の上、人手**で行う（settings は直接 Edit 不可 → `/update-config`）。手順は生成物の
  `WIRING.md` に印字される。

## 目的
- 再発ミスの記述 → block パターンを持つ fail-closed hook scaffold を 1 コマンドで生成する。
- 生成 hook が fail-closed 必須要素（stdin JSON 読取・jq→python3 フォールバック・parse 失敗
  exit 2・block ヘルパ・BSD grep 互換）を**確実に**持つことを生成時に検証する。
- block/pass/fail-closed の 3 ケースを standalone 実走可能な形で同時生成し、配線前の
  セルフテスト（house 規約「フック導入はセルフテスト必須」）を満たせるようにする。

## 実行手順
1. 止めたいミスとパターンを決めて生成する。
   ```sh
   bash scripts/gate-forge.sh --name state-claim-guard --tool Bash \
     --mistake "文書ベースの状態推定禁止: 再実測なしに状態を list 化しない" \
     --pattern "git reset --hard" \
     --block-msg "git reset --hard は履歴を破壊する" --alt "git stash / git revert"
   # Write/Edit を守るなら --tool Write（field は file_path を自動選択）
   # 正規表現で照合するなら --regex（既定は fixed-string / BSD grep 互換）
   # --pattern 省略で TODO モード（plumbing だけ生成し pattern は後で実装）
   ```
   生成物（既定 `<repo>/.gate-forge-staging/<slug>/`・`--out-dir` で変更可）:
   - `pre-<slug>.sh`（fail-closed hook・`chmod +x` 済）
   - `test-<slug>.cases.sh`（block / pass / fail-closed の 3 ケース・standalone 実走可）
   - `WIRING.md`（配線手順 + L3/self-lockout 警告）
2. TODO モードなら hook の `# --- TODO ...` と cases の BLOCK case を実装する。
3. 配線前にセルフテスト（副作用なし）:
   ```sh
   bash <out>/test-<slug>.cases.sh <out>/pre-<slug>.sh   # ✅ PASS を確認
   ```
4. **責任者 承認後**、`WIRING.md` に従い人手で配置（`cp`）→ `test-hooks.sh` にケース移植
   → `/update-config` で settings.json 登録 → `bash .claude/hooks/test-hooks.sh` 全通過を**実測**
   してから完了とする（merged/配置 ≠ 反映・原則 P1）。

## 引数
| 引数 | 必須 | 意味 |
|---|---|---|
| `--name` | ○（`--self-test` 時は不要） | hook slug（kebab `[a-z0-9-]`・先頭 `pre-` は許容）。出力 = `pre-<slug>.sh` |
| `--mistake` | ○（同上） | 止めたいミスの一文（header コメント + block メッセージに載る＝WHY を残す） |
| `--tool` | 任意 | 守る tool（既定 `Bash`）。`Bash`→`command` / `Write`/`Edit`→`file_path` を自動選択 |
| `--field` | 任意 | 抽出する `tool_input` キーを明示上書き |
| `--pattern` | 任意 | block する文字列。省略で TODO モード（plumbing のみ） |
| `--regex` | 任意 | pattern を ERE で照合（`grep -Eq`）。既定は fixed-string（`grep -Fq`） |
| `--block-msg` / `--alt` | 任意 | block 時の理由 / 安全な代替（省略時は mistake を流用） |
| `--out-dir` | 任意 | staging 出力先（既定 `<repo>/.gate-forge-staging/<slug>`） |
| `--force` | 任意 | 既存生成物を上書き（既定は上書き拒否 exit 4） |
| `--self-test` | — | hermetic 決定論テスト（gh/aws 非依存・temp 実生成 → 検証） |

## 決定論 --check
```sh
bash scripts/gate-forge.sh --self-test
```
- **exit 0** = PASS。temp に hook / cases を**実生成**し、① `validate_hook`（shebang・`INPUT=$(cat)`・
  `jq -r`・`python3` フォールバック・`exit 2`・`exit 0`・`block()` + `bash -n`）が Bash/Write/ERE
  で通る ② **負制御×2**（`exit 2` を落とした hook・stdin 読取を落とした hook は validate が**落ちる**
  ＝検証器が本物・ハードコード成功でない）③ **実挙動**（match→exit 2 / benign→0 / 空フィールド→2 /
  非 JSON→2 の fail-closed）④ 生成 cases ファイルを standalone 実走し PASS ⑤ mistake テキストが
  hook に埋め込まれている（WHY 保持）⑥ TODO モードでも plumbing（fail-closed）が健全、を assert する。
- **exit 1** = いずれかで不一致（ジェネレータ / 検証器の破損）。
- 依存は bash + python3 標準ライブラリのみ・hermetic（外部 API・既存 hooks/settings に触れない）。

## 参照
- 生成 hook の手本（house 既存 fail-closed hook）: `.claude/hooks/pre-file-protect.sh` /
  `.claude/hooks/pre-bash-safety.sh`
- セルフテスト規約 / runner 形式: `.claude/hooks/test-hooks.sh`
- 監査 + 提案のみ・適用は L3 の同方針 skill: `skills/ci-required-check-installer/SKILL.md`
- 承認レベル（Level 3 = self-lockout）: `docs/principles.md`
- 規範: 原則 P3（可逆性 × 外部到達 × 金銭/法的）/ 原則 P1（自己申告禁止・決定論ゲート）
- フック規約（fail-closed=exit 2・stdin JSON・BSD grep 互換・導入はセルフテスト必須）:
  `docs/incidents.md`「ハーネス・フック」節
