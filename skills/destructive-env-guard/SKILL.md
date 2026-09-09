---
name: destructive-env-guard
description: 破壊/env 変更コマンド（rm -rf・force push・reset --hard・aws delete・DNS・runtime env・secret rotation）が pre-bash-safety.sh でブロックされるかを読取専用で監査し、未カバーの gap に追加候補パターンを提示する（フックは改変しない・適用は人手/L3）。トリガー語彙: destructive audit, 破壊コマンド監査, env guard, self-lockout 監査, pre-bash-safety カバレッジ, フックの穴, gap 検出
---

# Destructive / Env Guard（カバレッジ監査 + 提案のみ・適用は L3）

> 破壊系・self-lockout 系コマンドの代表 canary を、既存の fail-closed フック
> `.claude/hooks/pre-bash-safety.sh` に **read-only で通して** ブロック有無を観測し、
> 素通り＝gap（フックの穴）を検出して追加候補パターンを **印字だけ** する。
> **この skill はフックを絶対に改変しない**（`settings.json` 登録・`.githooks` 設置もしない）。

## ⚠️ 承認レベル（原則 P3）

- gap を埋める hardening（`pre-bash-safety.sh` へのパターン追加）は self-lockout に触れうる **Level 3**。
  **適用は人手** — 責任者 事前承認後に 運用担当 が手動で行う。この skill と `scripts/destructive-audit.sh` は
  **監査（read / フックを実行して exit code を見るだけ）＋提案（印字）まで**。
- cloud 系 L3（aws delete / DNS / runtime env / secret rotation）は現状 **policy（L3 承認）だけ**で守られ、
  fail-closed gate には未搭載。本監査はこの差（gate ⊂ policy）を可視化する観測器。
  **harness gate ⊇ policy** を満たす方向の提案であり、gate を policy で緩める提案は出さない。

## 目的

- pre-bash-safety.sh の破壊/env カバレッジを **live probe** で確認し、gap を機械検出する。
- 「文書ベースの状態推定禁止」の原則どおり、フックのソースを読むだけでなく **実際に canary を通して**
  ブロック挙動を実測する（source に痕跡が有っても正規表現が canary を捉えるとは限らないため）。

## 実行手順

1. 監査（読取専用・フック改変なし）:
   ```
   bash scripts/destructive-audit.sh
   ```
   - 各 category の canary を pre-bash-safety.sh に通し `covered / gap / unknown` を印字。
   - `covered` = exit 2（ブロック）/ `gap` = exit 0（素通り）/ `unknown` = それ以外（0 と扱わない）。
   - gap があれば追加候補 grep パターンを印字（**実行しない**）。
   - フック不在/実行不能 → exit 3（gap 0 と誤読しない＝「不明」）。
2. gap を埋める場合は、印字された候補を土台に **責任者 承認を得てから** 運用担当 が手動で
   `pre-bash-safety.sh` を編集し、`.claude/hooks/test-hooks.sh` に block/pass 両ケースを追加してから配線する
   （フック導入はセルフテスト必須・common-mistakes 準拠）。
3. 適用後にこの監査を再実行し、当該 category が `covered` に転じたことを **実測**で確認する
   （設定 ≠ 反映・原則 P1）。

## 決定論 --check

```
bash scripts/destructive-audit.sh --self-test
```
- **exit 0** = probe→classify（exit code → covered/gap/unknown）+ build_json + static_has_pattern の
  各ロジックが hermetic fixture（temp の擬似フック）全ケースで一致。
- **exit 1** = いずれかのケースで不一致（ロジック破損）。
- self-test は **実フック / gh / aws / network に非依存**。temp fixture を自作して
  「ブロックする category=covered・しない category=gap」の判定を検証する（ハードコード成功なし・原則 P1）。

## 参照

- 監査対象フック: `.claude/hooks/pre-bash-safety.sh`（読むだけ・改変しない）
- スクリプト: `scripts/destructive-audit.sh`
- 承認レベル: `docs/principles.md`（Level 3 = self-lockout: 権限/secret/監視/DNS/runtime env）
- フック導入規約: `docs/incidents.md`（フック導入はセルフテスト必須 / BSD grep 互換）
