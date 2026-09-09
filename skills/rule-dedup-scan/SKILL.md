---
name: rule-dedup-scan
description: CLAUDE.md と docs/*.md の間で重複・矛盾するルール行を決定論的に検出し、常駐コンテキストの二重管理を解消する。トリガー語彙: ルール重複検出, rule dedup, CLAUDE ルール重複, 常駐ルール重複, ルール矛盾検出, ルール集約, rule dedup scan
---

# rule-dedup-scan（CLAUDE.md × rules 間のルール重複検出）

> CLAUDE.md と `docs/*.md` はどちらも常駐（毎ターン読まれる）コンテキストである。
> 同じルールが両方に書かれると、① トークンを二重に消費し ② 片方だけ更新されて矛盾を生む。
> 両者の箇条書きルール行をトークン化して近似重複（Jaccard 類似度）を機械検出し、
> 「どちらか一方に一本化する」集約を促す（原則 P1 自己申告禁止・決定論ゲート）。

## 目的

- ルート `CLAUDE.md` の箇条書きルール行と `docs/*.md` の箇条書き行を突合する
- 正規化トークン集合の **Jaccard 類似度 ≥ 0.7** のペアを近似重複として列挙する
- 重複が出たら、**主所属を 1 箇所に決めて他方を削除／ポインタ化**し、常駐の二重管理を解消する
- `CLAUDE.md` 欠如は「対象無・観測継続」として exit0（欠如≠違反・空≠ゼロ件）

## 重複は「1 ルール = 1 正本」に寄せる

- 同一趣旨のルールが 2 箇所にあると、更新漏れで矛盾（片方が旧版）が発生し、どちらが正か判定できなくなる。
- 解消は **正本を 1 つ決める**方向のみ。CLAUDE.md は概要・`docs/` は詳細という役割分担（ファイル管理ルール）に沿って、詳細側に寄せて CLAUDE.md からは削除するか、参照ポインタに置き換える。
- ルールを増やして重複を塗り潰さない（Counter-Convergence: 要求外の追記禁止）。CLAUDE.md 200 行上限（pre-commit hook）も踏まえ、常駐は減らす方向で解消する。

## 実行手順

1. `python3 scripts/harness-lint.py rule-dedup` を実走し、近似重複の件数と該当ペアを確認する
   - 入力: ルート `CLAUDE.md` ＋ `docs/*.md`（`--rules-dir DIR` で差替可・`--root DIR` で repo ルート差替可）
   - `--json` で機械可読出力（`items[]` = `claude` / `rule_file` / `rule` / `jaccard`）
   - 出力例: `~0.78 [common-mistakes.md] - **force push 禁止** — …`
2. 重複ペアごとに正本を 1 つ決め、他方を削除するか 1 行ポインタ（例: 「詳細: `standards/…`」）に置き換える
   - CLAUDE.md 側は概要のみ・詳細は `docs/` へ寄せる（`docs/practices.md` の役割分担に従う）
3. `--check` で違反 0 を確認してから CLAUDE.md / rules をコミットする（`python3 scripts/harness-lint.py rule-dedup --check`）
4. 月次 `/harness-review` の C 項（ルール整合性・`docs/` と `standards/` の重複がないか）で本 check の exit と重複件数を記録する

## 決定論 --check（コマンドと exit の意味）

```
python3 scripts/harness-lint.py rule-dedup --self-test
```

- **exit 0** = self-test PASS: hermetic temp fixture（CLAUDE.md と rules/cm.md に同一の「force push 禁止」ルールを共有させ、固有ルールは非重複）で
  Jaccard 近似重複検出が期待通り（重複 ≥ 1 件を assert）。加えて CLAUDE.md 欠如で「対象無・観測継続」exit0 になる耐性も確認する。fixture は tempfile に作り実行後に削除する（リポジトリに残さない）。
- **exit 非0** = FAIL: 箇条書き抽出・Jaccard 集計・対象欠如耐性のいずれかが壊れている（stderr に失敗内容を列挙）。

運用時の違反ゲート:

```
python3 scripts/harness-lint.py rule-dedup --check
```

- **exit 0** = 対象あり かつ 重複 0（または CLAUDE.md 欠如）
- **exit 1** = 対象あり かつ 近似重複 > 0（該当ペアと類似度を stdout に印字）

## 参照

- 実装: `scripts/harness-lint.py` サブコマンド `rule-dedup`（`run_rule_dedup` / `_bullet_lines` / `_norm_tokens` / `_jaccard` / `_rule_files`）
- 検査対象: ルート `CLAUDE.md` ＋ `docs/*.md`（常駐ルール）
- 姉妹 skill: resident-context-budget / resident-context-slim（常駐行数削減）/ rule-promotion-extract（rules→フック昇格）/ trigger-vocab-dedup（同じ決定論ゲート系列）/ harness-review（月次 C 項）
- 設計原則: 原則 P1（自己申告禁止・決定論ゲート・閾値は緩めない）/ ハーネス再設計 H1（常駐コンテキスト最小化）/ `docs/practices.md`（CLAUDE.md=概要・rules=詳細の役割分担）
