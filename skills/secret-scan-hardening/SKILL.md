---
name: secret-scan-hardening
description: secret 検出正規表現セット（policy）の「既知 API キー形式」に対する網羅率を読取専用で監査し、未カバー形式と追加パターン案を提示する（フックは改変しない・適用は人手）。トリガー語彙: secret scan, シークレット検出強化, secret scan hardening, API キー漏洩防止, secret パターン網羅率, secret-scan-audit, 検出網羅監査
---

# Secret Scan Hardening（監査 + 提案のみ・フック改変なし）

> 既知 API キー形式の corpus に対し、与えた secret 正規表現セット（policy）の
> **検出網羅率を read-only で監査**し、未カバー形式ごとに追加パターン案を印字する。
> **この skill は security フックを絶対に改変しない**（`.claude/hooks/pre-commit-quality.sh` は読むだけ）。

## ⚠️ 承認レベル（原則 P3）

- `pre-commit-quality.sh` は **fail-closed の security フック = harness gate の一部**。
  提案パターンの**フックへの適用は人手**で行い、gate を触るため **責任者 レビュー後**とする（自己適用禁止）。
- この skill と `scripts/secret-scan-audit.py` は **監査（read）＋提案（印字）まで**。
  フック本体・settings.json への書込みは一切しない（policy で gate を緩めない・harness gate ⊇ policy）。

## Harness KPI

- **secret 検出網羅率 → 増**。未カバー形式（xapp- / ghr_ / glpat- / npm_ / SG. / SK / hf_ / GOCSPX- 等）を
  検出し、hardening 候補を可視化する観測器。網羅率が上がると漏洩コミットの取りこぼしリスクが下がる。

## 実行手順

1. 監査（読取専用・既定は内蔵 DEFAULT policy = 現行フックのミラー）:
   ```
   python3 scripts/secret-scan-audit.py
   ```
   - 網羅率と `[GAP] 未カバー形式` + 提案パターンを印字。gap ありで **exit 1**、gap なしで **exit 0**。
   - 別の policy を監査する場合（1 行 1 正規表現・`#` コメント可）:
     ```
     python3 scripts/secret-scan-audit.py --patterns-file <path>
     ```
2. 提案された未カバー形式のうち、運用組織 が実際に使う可能性のある鍵種を選ぶ
   （AWS/GitHub/Stripe/Supabase/Slack/Google 系を優先。無関係な形式は採用しない — 過剰追加禁止）。
3. **責任者 レビューを得てから**、選んだパターンを人手で `pre-commit-quality.sh` の
   `SECRET_PATTERNS` に追記する（この skill は追記しない）。BSD grep 互換（`grep -P` 禁止・
   `[[:space:]]` を使う）で書き、`.claude/hooks/test-hooks.sh` に block/pass 両ケースを追加してから配線する。
4. 追記後は `scripts/secret-scan-audit.py` を再実行し、対象形式が covered に移ったことを **実測**で確認する
   （merged/編集 ≠ 反映。live 検証で証明・原則 P1）。

## 決定論 --check

```
python3 scripts/secret-scan-audit.py --self-test
```
- **exit 0** = 網羅率算出ロジック（正規表現コンパイル → sample マッチ → covered/uncovered 分類 →
  不正パターンの graceful skip → 提案パターンの自己マッチ）が内蔵 fixture 全ケースで一致。
- **exit 1** = いずれかのケースで不一致（ロジック破損 or corpus の sample/suggest 不整合）。
- self-test は gh/aws 非依存・hermetic（内蔵 fixture のみ・外部書込なし・ハードコード成功なし）。

## 参照

- 承認レベル: `docs/principles.md`（harness gate ⊇ policy・self-lockout は Level 3）
- 監査対象フック（読むだけ）: `.claude/hooks/pre-commit-quality.sh` の `SECRET_PATTERNS`
- スクリプト: `scripts/secret-scan-audit.py`
- フック配線テスト: `.claude/hooks/test-hooks.sh`（追記時に block/pass 両ケースを追加）
