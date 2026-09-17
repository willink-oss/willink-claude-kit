---
name: start
description: "初めて kit を使う人に、導入 → 測る → 決める → 作る → 出す の順序を示し、各段の「終わり」を既存の決定論コマンドの exit code で確かめさせる。/build・/goal-loop・maker-checker-relay・/oneshot・/mvp の使い分けと、/oneshot までに揃える 6 つの材料を含む。トリガー語彙: start, はじめに, 初めて使う, 使い方, どこから始める, getting started, onboarding, オンボーディング, 実装の流れ, oneshot までの手順"
allowed-tools: Bash, Read, Glob, Grep
---

# start — 初めての人のための順序（導入 → 測る → 決める → 作る → 出す）

> この kit は「提供するもの」が多く、初めての人は**どの順で何をすれば実装が進むか**が読み取れない。
> この skill は順序だけを示す。各段の「終わり」は文章でなく**既存の決定論コマンドの exit code**で確かめる
> （自己申告を採点に使わない）。「入っている」と「動いている」は別（文書 = plan / live = state）。

## 0. いま居る段を測る

上から順に叩き、**最初に終わりの印が出ない段**が「いま居る段」。それより先を読まなくてよい。

| 段 | 確かめるコマンド | 終わりの印 |
|---|---|---|
| 1 導入 | `bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/check-kit-enabled.sh"`（kit 同梱） | exit 0。「入っている」でなく**ロードされている** |
| 2 測る | `bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/pulse-precheck.sh"`（kit 同梱・read-only） | stack / CI / open PR の probe 行が出る。`❓` は不明であって 0 ではない |
| 3 決める | §3 の表 | 進め方を 1 つ選べている |
| 4 作る | `/build` | dev-tester **PASS** かつ dev-reviewer **PASS** |
| 5 出す | `gh pr view --json statusCheckRollup` | PR が開いていて CI 緑。**merge は人が押す** |

## 1. 導入

- `.claude/settings.json` に `extraKnownMarketplaces` と `enabledPlugins` の 2 ブロック（README「導入方法」）。値は boolean の `true`。
- 終わりの印は `check-kit-enabled.sh` の exit 0。exit 1 なら印字された診断に従う（配列で書かれていて「有効」表示のまま何もロードされない事故が既知）。
- 任意: `.claude/skills/project-standards/SKILL.md`（雛形は `examples/project-standards-template/`）。無くても warning だけで動く。

## 2. 測る（`/pulse`）

作る前に **1 回** `/pulse`。理由は 2 つ:
- test / lint / typecheck / build の**実コマンド**と CI の required check が分かる。これが後で完了条件（DoD）の材料になる
- 状態表の `❓` は「測れていない」。0 件・完了と読まない。`/pulse` は一切 mutate しない

## 3. 決める（進め方の使い分け）

| 状況 | 使う | 人の位置 |
|---|---|---|
| 仕様を対話で詰めながら進めたい。各 phase に人が居られる | **`/build`**（基本形） | in the loop |
| 決定論のゴール（カバレッジ閾値・lint 0・特定テスト緑）を反復で詰めたい | **`/goal-loop`** | 停止は `--check` の exit code と `--max` |
| 実装とレビューを分けて「test 緑 **かつ** 指摘 0」まで回したい | **`maker-checker-relay`** | Checker は読取専用 |
| 仕様を細かく詰め終えていて、人はループの外から見守りたい | **`/oneshot`** — 設計済・**未実装** | on the loop |
| 仕様が揺れたまま最小で速く | **`/mvp`** — 名称のみ・**未着手** | — |

未実装の 2 つは、今日は `/build` + `goal-loop.sh --check` で同じ規律を手で回す（§6）。

## 4. 作る（`/build` の 5 phase）

1. **探索** — 影響が 3 軸以上のときだけ dev-explorer（read-only・最大 3 並列）。1〜2 軸なら直接読む
2. **計画** — >1 file または >50 行のとき dev-planner。UI を触るなら accessible name / role / state をここで決める（UI 非変更なら「a11y: N/A」と書く。空欄は「検討していない」と同じ）
3. **実装** — メイン Claude が Edit / Write で書く。subagent に委譲しない
4. **検証** — dev-tester ∥ dev-reviewer を並列。両方 PASS まで「終わった」と言わない（速いことと終わったことは別）
5. **修正 / commit** — commit は「なぜ」を書く。パス束縛（`git commit -m ... -- <paths>`）

作業は worktree で行い、主 checkout の HEAD は動かさない。

## 5. 出す

PR を開いて止まる。merge は人が押す。報告は **4 段**（何を言われて何をやったか → 結論 → 詳細 → まとめ）で、件数は必ず分母つき（「走査 N 件のうち M 件」。「0 件」は走査した証拠と一緒に）、判断を仰ぐ項目は 5 点つきで 3 件まで。読む人はこの会話の経緯を覚えていない前提で、`scripts/report-shape-check.py` に通してから出す（`skills/report-shape/SKILL.md`）。

## 6. `/oneshot` までに揃える 6 つの材料

`/oneshot` は入口で利用者と契約（完了条件・触ってよい範囲・予算）を作ってから無人区間に入る。
契約を作る 6 問に**答えられる材料**が揃っているかを先に見る。1 つでも揃わなければ、まだ `/build` の段に居る。

| # | 問 | 揃っている印 |
|---|---|---|
| 1 | 何ができれば「終わり」か。**人が目で見る言い方を、コマンドで判定できる言い方に** | §2 で test の実コマンドが出ている。新規テストの置き場が決まっている |
| 2 | いま動いているもので、壊してはいけないものは何か | CI の required check が 1 つ以上ある |
| 3 | 触ってよい場所 / 触らない場所 | ディレクトリで言える |
| 4 | 画面を触るか | 触るなら `scripts/a11y-static-check.py` が回る（exit 3 = 走査 0 件 = 不明） |
| 5 | 新規の判定ごとに、**何を壊したら赤になるべきか** | 「壊す 1 手」を `sed` 等の決定論コマンドで言える |
| 6 | いつまでに要るか・途中を見たいか・失敗が続いたらどうしたいか | 答えから試行回数 / 時間 / トークンを導く。**機械が既定値を先に出さない** |

それまでの手動同等（`/oneshot` と同じ規律を `/build` の外側で回す）:

```sh
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/goal-loop.sh" --reset --state .goal-loop-state
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/goal-loop.sh" \
  --goal "<1 文>" --check "<test && lint && typecheck>" --max <N> --state .goal-loop-state
# exit 0 = 達成 / 1 = 続ける（1 歩だけ直す）/ 2 = 上限。上限に当たったら人へ
```

実装前に `--check` が**赤**であることを 1 回確かめる（red-first）。最初から緑なら、その検査は仕事をしていない。

## 7. 止める（ゲートは観測してから昇格）

段階 1 観測（数字だけ・2 週間）→ 段階 2 CI で落とす（可逆）→ 段階 3 hook（fail-closed・自己テスト必須）。
順序は `docs/harness/wiring.md`、原則は `docs/harness/principles.md`。**一度外したゲートは戻らない**ので、初日に全部 fail-closed にしない。

## セッション衛生

new task = new session。`/pulse` は作っている途中の作業とは別セッションで。同じ変更の 実装 → test → commit は同じセッションでよい（kit `docs/session-hygiene.md`）。
