---
name: memory-distill
description: 自動メモリの索引（memory/MEMORY.md）と実体（memory/*.md）を突合し、孤児(未索引)・リンク切れ・重複エントリを決定論的に蒸留する。トリガー語彙: メモリ蒸留, memory distill, MEMORY 索引, メモリ孤児, memory orphan, 索引リンク切れ, メモリ重複エントリ
---

# memory-distill（自動メモリ索引と実体の蒸留）

> セッション横断の自動メモリ（`~/.claude/projects/*/memory/`）は `MEMORY.md` が索引・`*.md` が実体。
> 実体を追加/削除/リネームしたのに索引を更新し忘れると、**孤児（実体あるが未索引）** や
> **リンク切れ（索引にあるが実体無）** が溜まり、索引が state を正しく指さなくなる。
> さらに似た説明文の**重複エントリ**が増えると LLM の想起が非決定的になる。
> 全 `MEMORY.md` リンクと `memory/*.md` 実体を突合し、これら 4 種の逸脱を機械検出する
> （原則 P1 自己申告禁止・決定論ゲート）。

## 目的

- `~/.claude/projects/*/memory/MEMORY.md` の `[file](file) — desc` リンクを抽出し、実体（同ディレクトリの `*.md`）と突合する
- **リンク切れ**（索引にあるが実体無）・**孤児**（実体あるが未索引）・**索引重複**（同一 basename を複数行が主張）・**説明近似重複**（説明文が高類似のエントリ対）を列挙する
- 逸脱が出たら索引を実体に合わせて蒸留する（欠落エントリ追加 / 死んだリンク除去 / 重複統合）
- memory dir or `MEMORY.md` 欠如は「対象無・観測継続」として exit0（欠如≠違反・空≠ゼロ件）

## 蒸留は「索引 = 実体の忠実な写像」に寄せる

- 索引は plan、実体が state。両者が食い違ったら **実体を正として索引を直す**（索引に合わせて実体を消さない）。
- 孤児 → 索引に 1 行追加。リンク切れ → 参照元行を削除、または実体を復元。
- 説明近似重複 → 意味が同一なら 1 エントリに統合、意味が別なら説明文を固有化して類似度を下げる（曖昧な包括説明を増やして重複を隠さない — Counter-Convergence: 要求外の追記禁止）。

## 実行手順

1. `python3 scripts/harness-lint.py memory-distill` を実走し、索引数/実体数と逸脱件数を確認する
   - 入力: `<memory>/MEMORY.md` と `<memory>/*.md`（`--memory-dir DIR` で差替可・`--root DIR` で repo ルート差替可）
   - `--json` で機械可読出力（`items.broken_links` / `items.orphans` / `items.dup_index` / `items.dup_desc` / `indexed` / `on_disk`）
   - 出力例: `索引リンク切れ: ghost.md` ／ `孤児(未索引): orphan.md` ／ `説明近似重複: a.md ~ b.md`
2. 逸脱が出たら種別ごとに蒸留する:
   - リンク切れ → `MEMORY.md` の該当行を削除、または欠落実体を復元
   - 孤児 → `MEMORY.md` に `- [x.md](x.md) — 要約` を追記
   - 索引重複 → 重複行を 1 本に統合
   - 説明近似重複 → エントリ統合、または説明文を固有化
3. `--check` で違反 0 を確認してから索引の変更をコミットする（`python3 scripts/harness-lint.py memory-distill --check`）
4. 月次 `/harness-review` の F 項（Memory 活用）で本 check の exit と逸脱件数を記録する

## 決定論 --check（コマンドと exit の意味）

```
python3 scripts/harness-lint.py memory-distill --self-test
```

- **exit 0** = self-test PASS: hermetic temp fixture（索引に実体無しの `ghost.md` と未索引の `orphan.md` を仕込み、
  リンク切れ・孤児がともに検出され違反≥2 になることを assert）。加えて memory dir 欠如で
  「対象無・観測継続」exit0 になる耐性も確認する。fixture は tempfile に作り実行後に削除する（リポジトリに残さない）。
- **exit 非0** = FAIL: 索引パース・突合・重複集計・対象欠如耐性のいずれかが壊れている（stderr に失敗内容を列挙）。

運用時の違反ゲート:

```
python3 scripts/harness-lint.py memory-distill --check
```

- **exit 0** = 対象あり かつ 逸脱 0（または対象欠如）
- **exit 1** = 対象あり かつ 逸脱 > 0（リンク切れ/孤児/重複を stdout に印字）

## 参照

- 実装: `scripts/harness-lint.py` サブコマンド `memory-distill`（`run_memory_distill` / `_parse_memory_index` / `_jaccard` / `_norm_tokens`）
- 検査対象: `~/.claude/projects/*/memory/MEMORY.md`（索引）と同ディレクトリの `*.md`（実体）
- 姉妹 skill: knowledge-index-guard（`<TARGET>/assets/knowledge` 索引整合）/ trigger-vocab-dedup / rule-promotion-extract（同じ決定論ゲート系列）/ harness-review（月次 F 項）
- 設計原則: 原則 P1（自己申告禁止・決定論ゲート・閾値は緩めない）/ ハーネス再設計 H1（Memory 活用）
