---
name: consumer-finding-ledger
description: "consumer（実案件）で出たハーネスの課題 — ゲートの誤検知・見逃し・標準の抜け・不便・バグの型 — を作業中に 1 行ずつ台帳へ記録し、正本へ持ち帰る（sync → PR）。記録は決定論の check で整合を検査し、URL・org/repo パス・認証情報は入る手前で拒否する。トリガー語彙: 課題を記録, ハーネスの課題, ゲートの誤検知, 見逃した, 持ち帰り, finding sync, 台帳へ記録, harness finding, 誤検知を記録"
---

# consumer-finding-ledger（consumer の課題を正本へ持ち帰る）

> ハーネスは consumer で使われて初めて壊れ方が分かる。作業中に気づいた課題を **その場で 1 行**書き、
> 区切りで **正本の台帳へ PR** する。正本側で triage し、fixture / hook / 標準へ昇格する。
> 「後でまとめて書く」は書かれない（原則 P1: 自己申告でなく記録に残った事実で判断する）。

## いつ使うか（consumer 側・作業中）

| 起きたこと | kind | evidence |
|---|---|---|
| ゲートが正しい変更を止めた | `false-positive` | 止めたコマンドと exit code（**必須**） |
| ゲートが通したが、後で問題だと分かった | `missed` | 通ったコマンドと、問題が分かった観測（**必須**） |
| 止めるべきだが、そもそもゲートが無い | `gap` | あれば |
| 動くが遅い・分かりにくい・手順が多い | `friction` | あれば |
| 開発標準がこのスタックで曖昧 / 誤り / 抜け | `standards` | 該当の節 |
| アプリのバグで、fixture にできる型 | `bug-pattern` | 再現手順と exit（**必須**） |
| 文書の誤り | `docs` | — |

**ゲートが止めたときに、ゲート側を緩めて通さない。** 記録して先へ進み、正本で直す。

## 実行手順（consumer 側）

1. 記録する（1 行・200 字以内・URL と org/repo パスは書けない）

   ```bash
   python3 .claude/willink-kit/engines/finding.py add \
     --kind false-positive --component pre-commit-shell-lint.sh --stage ios \
     --summary "grep の [[:space:]] 化の案内が BSD 互換の説明を欠く" \
     --evidence "git commit → exit 1（pre-commit-shell-lint）"
   ```

   台帳は `.claude/harness-findings.jsonl`（**コミットする**・オフラインでも書ける）。
   `consumer` は既定でリポジトリのディレクトリ名（`PH_CONSUMER` / `--consumer` で上書き）。

2. 整合を見る（CI にも入れる）

   ```bash
   python3 .claude/willink-kit/engines/finding.py check --check --allow-missing
   ```

3. 区切り（PR を出す前・セッションの終わり）で正本へ持ち帰る

   ```bash
   python3 .claude/willink-kit/engines/finding.py sync --harness ~/GitHub/willink-harness          # preview
   python3 .claude/willink-kit/engines/finding.py sync --harness ~/GitHub/willink-harness --push   # worktree → commit → push → PR
   ```

   `--push` は origin/main から一時 worktree を切るので、正本の clone の作業状態を汚さない。
   `gh` が無い / 権限が無い場合は `--apply` で正本の clone に追記し、commit と PR を手で行う。

## 実行手順（正本側・triage）

```bash
python3 scripts/finding.py list --status open
python3 scripts/finding.py triage --id <consumer>/<id> --status fixed --pr <PR 番号>
python3 scripts/finding.py triage --id <consumer>/<id> --status promoted --promoted-to fixtures/<name>
python3 scripts/finding.py stats
```

- 昇格先は 3 つ: **fixture**（`bug-pattern`）/ **hook・engine の修正**（`false-positive` `missed` `gap`）/ **標準の改訂**（`standards`）
- 昇格時は人が 1 件ずつ、顧客名・URL・金額・個人情報が無いことを確認する（fixture 昇格と同じ原則）
- `wontfix` にするときは `--note` に理由を残す

## 決定論 --check（コマンドと exit の意味）

```
python3 scripts/finding.py check --file findings/consumer-findings.jsonl --check
```

- **exit 0** = 走査 N 行すべて schema に合致・id 重複なし・URL / org/repo / 認証情報なし
- **exit 1** = 違反あり（行番号と理由を列挙）
- **exit 2** = 台帳が無い（**0 件ではなく未作成**。consumer の CI では `--allow-missing`）

```
python3 scripts/finding.py --self-test
```

- **exit 0** = hermetic な tmp で add / check（block・pass）/ sync（preview・apply・再 apply）/ triage が期待どおり（27 ケース）

## 参照

- エンジン: `scripts/finding.py`
- 正本の台帳と運用: `findings/README.md`
- 昇格先: `fixtures/`（再現ケース）/ `core/hooks/`・`scripts/`（ゲート）/ `standards/development-standards.md`
- 姉妹 skill: `mistake-rule-distill`（ミス経緯 → ルール蒸留）/ `rule-promotion-extract`（advisory → blocking 昇格）
- 設計原則: 原則 P1（自己申告禁止・決定論ゲート）
