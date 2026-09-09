---
name: knowledge-index-guard
description: <TARGET>/assets/knowledge の索引（<TARGET>/assets/knowledge-base.md）が実体と一致し broken link=0 であることを保証する。トリガー語彙: ナレッジ索引, knowledge index, 索引ガード, index guard, broken link チェック, knowledge-base 整合性, 索引 drift
---

# ナレッジ索引ガード（knowledge-index-guard）

> Harness Ladder H3（ブロッキング検証）。`<KNOWLEDGE_DIR>/` の実体と索引 `<TARGET>/assets/knowledge-base.md` の AUTO-INDEX 区画が一致し、相対リンク切れ 0 であることを決定論ゲートで保証する。
> 2026-06-11 整合性監査で機械生成に切替済（手動 index はカバー率 24.5%・リンク切れ 26% まで腐敗していた）。本 skill はその生成物が drift していないことを継続検証する。

## 目的

- 索引の drift（ナレッジ追加/削除/リネーム後に索引を再生成し忘れた状態）を機械的に検出する
- 索引内の相対リンク切れ（存在しないファイルへの参照）を 0 に保つ
- 自己申告禁止（原則 P1）: 「索引は最新のはず」で済ませず、`--check` で実体と突き合わせる

## 実行手順

1. リポジトリルートで決定論 --check を実走する（下記）
2. exit 0 → 索引は健全。何もしない
3. exit 1 → drift or broken link あり。原因に応じて対処:
   - drift（AUTO-INDEX が実体と不一致）→ `python3 scripts/regenerate-knowledge-index.py` で再生成しコミット
   - broken link（実体消失）→ 参照元を修正、または欠落ファイルを復元してから再生成
4. 再生成後にもう一度 --check を実走し exit 0 を確認してから完了とする

## 決定論 --check

```sh
python3 scripts/regenerate-knowledge-index.py --check
```

- **exit 0**: `<TARGET>/assets/knowledge-base.md` の AUTO-INDEX が `<KNOWLEDGE_DIR>/` の実体と完全一致し、索引内の相対リンクが全て存在する（broken=0）= 達成
- **exit 1**: AUTO-INDEX が drift している or broken link>0 = 未達（要再生成/修正）
- `--check` は**読取専用**でありファイルを一切書き換えない。書込は無引数実行（`python3 scripts/regenerate-knowledge-index.py`）のみが行う

## 参照

- 生成/検証スクリプト: `scripts/regenerate-knowledge-index.py`
- 索引本体: `<TARGET>/assets/knowledge-base.md`（AUTO-INDEX マーカー内は手動編集禁止）
- 月次レビュー C 項でも同スクリプトの broken links: 0 を確認する（`harness-review`（本バンドル外））
