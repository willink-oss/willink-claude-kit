---
name: report-shape
description: "報告と引き継ぎ文書の「形」を機械で守る。報告は 4 段（何を言われて何をやったか → 結論 → 詳細 → まとめ）・判断は 5 点で 3 件まで・件数は分母つき・内輪語は初出で説明、を report-shape-check.py が数え、HANDOFF などの状態文書は state-doc-shape-check.py が行数 floor（下げる方向のみ）と完了節の残留を止める。読む人はこの会話の経緯を覚えていない前提。トリガー語彙: 報告の型, 報告フォーマット, 4 段の報告, 判断を仰ぐ 5 点, 分母つき, 内輪語, 認知ドリフト, 認知負荷, HANDOFF の掃除, state doc floor, report shape"
allowed-tools: Bash, Read
---

# report-shape — 報告と状態文書の形を機械で守る

> 読む人はセッションを多数並行していて、**この会話の経緯を覚えていない**。経緯を前提にした報告は単体で読めない。
> 「結論から書く」を指示しても自己申告なので守られない（原則 P1）。だから**形を数える**。型の説明は `docs/harness/practices.md` §7。

## いつ使うか

- 作業の最終報告・PR 本文・引き継ぎ（wrapup）を書き終えたとき → `report-shape-check.py`
- HANDOFF / action list のような「上から順に読む」状態文書を更新したとき → `state-doc-shape-check.py`（CI に置く）

## 報告の検査

```sh
python3 scripts/report-shape-check.py report.md              # ファイル
pbpaste | python3 scripts/report-shape-check.py -             # 貼る前の本文（stdin）
python3 scripts/report-shape-check.py report.md --glossary glossary.json --strict
```

| 検査 | 何を数えるか | 落ちる条件 |
|---|---|---|
| S1 | 見出しに「何を言われて」「結論」「詳細」「まとめ」がこの順にあるか | 1 つでも欠ける・順が違う |
| S2 | `D1.` `D2.` … の判断項目が ①何を決めるか ②選択肢と結果 ③推奨と理由 ④判断しないと何が起きるか ⑤承認レベル を持つか。件数 ≤ `--max-decisions`（既定 3） | 5 点の欠落・上限超過 |
| S3 | 「N 件 / N 本 / N ファイル / N 行」の行に分母の印（`/`・中・のうち・走査・分母）があるか | **「0 件」に分母が無い**（他は advisory・`--strict` で違反） |
| S4 | `--glossary` の語が初出の行で説明されているか（`（…）`・`=`・とは・`:`） | advisory（`--strict` で違反） |
| S5 | 「別件:」の行数 | 数えるだけ |

exit `0` = 形が揃っている / `1` = 違反 / `2` = 空・読めない（0 件とは言わない）。

**代替手順**（落ちたときに機械が案内する直し方はそのまま通る）: 見出しを足す → S1 通過／「語（普通の言葉）」にする → S4 通過／判断を 3 件に絞って残りを件数に → S2 通過。

## 状態文書の検査

文書の先頭に印を 1 行置く: `<!-- shape: floor=180 -->`

```sh
python3 scripts/state-doc-shape-check.py docs/HANDOFF.md
```

| 検査 | 落ちる条件 | 直し方 |
|---|---|---|
| 行数 ≤ floor | 超過 | 完了した節を archive ファイルへ移す（floor を上げない） |
| floor は下げる方向のみ | git HEAD の印より大きい | 掃除して行数を減らす。上げる diff は通らない |
| 完了の見出しが無い（`## ✅` `### 1. ✅ 完了 …` `🗂️` `⏹`） | 1 節でも残る | archive へ移し、本体は 1 行にする |
| 表に取り消し線の行が無い（`\| ~~…~~`） | 1 行でも残る | 決まった行は消す（記録は台帳 / ADR） |

印も `--floor` も無ければ exit `2`（測れない）。`--done-prefixes` で完了の印を差し替えられる。

## 自己テスト

```sh
python3 scripts/report-shape-check.py --self-test      # 16 件（block / pass / 代替手順で通る）
python3 scripts/state-doc-shape-check.py --self-test   # 15 件（hermetic な git リポで ratchet も確認）
```

## 関連

- 型の説明と根拠: `docs/harness/practices.md` §7（Böckeler の guides + sensors・OpenAI「文脈で届かないものは存在しない」）
- 状態報告の裏取り（「merged / deployed」の断定に live 実測があるか）は kit の `live-state-verify-guard` が別に見る
- 同じ思想の停止プリミティブ: `scripts/goal-loop.sh`（自己申告でなく exit code）
