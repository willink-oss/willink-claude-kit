---
name: ci-required-check-installer
description: 対象リポ/ブランチの CI required status check を読取専用で監査し、未導入なら 責任者 承認後の適用コマンド例を提示する（適用はしない）。トリガー語彙: required check, branch protection 監査, CI ゲート未導入, required status check, 必須チェック監査, ブランチ保護
---

# CI Required Check Installer（監査 + 提案のみ・適用は L3）

> 対象リポの branch protection に「CI required status check」が設定されているかを
> **GET のみ**で観測し、未導入なら 責任者 承認後に 運用担当 が実行する適用コマンド例を印字する。
> **この skill は protection を絶対に適用しない**（PUT/POST/DELETE を一切実行しない）。

## ⚠️ 承認レベル（原則 P3）

- branch protection の**適用は Level 3**（self-lockout: 権限/CI ゲートの変更）。**責任者 事前承認必須**。
- この skill と `scripts/required-check-audit.sh` は**監査（read）＋提案（印字）まで**。
  適用コマンドは stdout に例示するのみで、実行は 責任者 承認後に 運用担当 が手動で行う。

## Harness KPI

- **CI required check 数 0 → 増**（`docs/principles.md` の KPI 表に沿う）。
  この skill は「未導入リポの検出」でこの KPI を前進させるための観測器。

## 実行手順

1. 監査（読取専用）:
   ```
   bash scripts/required-check-audit.sh --repo <owner/name> --branch <branch>
   ```
   - contexts >= 1 → 導入済（KPI 達成側）。監査完了。
   - contexts = 0 / 404(未保護) → 「未導入」判定 + 適用コマンド例を印字（**実行しない**）。
   - 404 以外の失敗 → 「不明」で exit 3（空出力を 0 件と誤読しない）。
2. 未導入なら、印字された手順（① 対象ブランチの check-run 名を実測 → ② 責任者 承認後 PUT）を
   **責任者 承認を得てから** 運用担当 が手動実行する。
3. 適用後に手順 1 を再実行し、contexts >= 1 を**実測**で確認してから「導入済」と記録する
   （merged/設定 ≠ 反映。live 実測で証明・原則 P1）。

## 決定論 --check

```
bash scripts/required-check-audit.sh --self-test
```
- **exit 0** = 抽出（contexts 本数）+ 分類（0→未導入 / >=1→導入済）ロジックが内蔵 fixture 全ケースで一致。
- **exit 1** = いずれかのケースで不一致（ロジック破損）。
- self-test は gh 非依存・hermetic（内蔵 JSON fixture のみ・外部書込なし）。

## 参照

- 承認レベル: `docs/principles.md`（Level 3 = self-lockout）
- スクリプト: `scripts/required-check-audit.sh`
