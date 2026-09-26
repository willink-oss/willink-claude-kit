---
name: oneshot
description: "細かく詰めた仕様 1 本から PR まで、人がループの外から見守る中で実装しきる（human on the loop）。最初に利用者と対話して決定論の完了条件（DoD 3 種・壊す 1 手・予算）を契約 oneshot.yaml に落とし、利用者が spec.md を見て「走らせてよい」と言ったら、/build の phase を goal-loop で外側から回す。完了は exit code だけ・PR を開いて止まる・merge しない。トリガー語彙: oneshot, /oneshot, ワンショット, 無人で実装, 任せて実装, 見守り実装, human on the loop, 契約して走らせる, 仕様から PR まで"
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent
---

# oneshot — 契約して、見守る中で PR まで実装しきる

> 設計: `docs/design/oneshot-mode.md`（正本）。
> **無人で回る部分の品質は、契約に書いた DoD の品質と同じにしかならない。** だから契約を作る段（Phase −1）は無人にしない。

## 0. 使うか決める

| 状況 | 使うもの |
|---|---|
| 仕様を細かく詰め終えていて、人が各 phase に張り付く価値が薄い | **この skill** |
| 仕様がまだ揺れている・相談しながら作りたい | `/build`（対話） |
| とにかく速く MVP を回したい | `/mvp`（別 skill・未実装）。それまでは `/build` |
| merge・deploy・公開・価格・顧客リポへの write が要る（Level 2 以上） | **使わない**（起動時に拒否する） |

## 1. 道具の在処（最初に 1 回だけ解決して、以後はそのパスをそのまま書く）

Bash の呼び出しごとに環境変数は消える。次を 1 回実行し、表示されたディレクトリ（以下 `$K`）を以後のコマンドに**文字どおり**書く。

```bash
python3 - <<'PY'
import json, os
root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
cands = [os.path.join(root, "scripts")] if root else []
cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
try:
    es = json.load(open(os.path.join(cfg, "plugins", "installed_plugins.json"))).get("plugins", {}).get("willink-claude-kit@iwillink", [])
    here = os.path.realpath(os.getcwd())
    es = sorted(es, key=lambda e: 0 if e.get("scope") == "project" and os.path.realpath(e.get("projectPath") or "") == here else 1)
    cands += [os.path.join(e["installPath"], "scripts") for e in es if e.get("installPath")]
except Exception:
    pass
cands += [os.path.abspath("scripts/engines")]   # 正本（willink-harness）の中で使うとき
hit = next((c for c in cands if os.path.isfile(os.path.join(c, "oneshot-preflight.py"))), "")
print(hit or "❓ oneshot-preflight.py が見つからない — claude plugin update willink-claude-kit@iwillink")
PY
```

使う道具（正本での置き場所。kit では `scripts/` 直下）:

| 道具 | 役割 |
|---|---|
| `scripts/oneshot-spec.py` | Phase −1 の機械側（stack 検出 → DoD 候補・壊す 1 手の候補・予算の導出・契約の下書き・spec.md） |
| `scripts/oneshot-preflight.py` | 契約の検査・red-first・毎周の判定（goal-loop の `--check`）・予算と割り込みの境界・mutation・state の更新 |
| `scripts/oneshot-report.py` | 報告書（分母つき・PR 本文に貼る） |
| `scripts/goal-loop.sh` | 止める仕組み（0 = GOAL MET / 1 = CONTINUE / 2 = CAP） |

hook（kit を有効にしていれば自動で効く・`oneshot/state.json` が無ければ何もしない）:
`pre-oneshot-scope.sh`（scope 外への書き込みと外への到達を止める）・`stop-oneshot-continue.sh`（未完了のまま手番を終えさせない）・`post-oneshot-elapsed.sh`（経過 / 予算を見せる）。

## 2. Phase −1 — 利用者と契約を作る（対話・時間の上限なし・**無人の指示はまだ効かない**）

ここは人が居る区間。**分からないことは止まって聞く**のが正しい。速さのために切り上げない。

1. **作業場所を分ける。** 主 checkout に触らない: `git worktree add -b oneshot/<slug> <dir> <base>` を作り、以後はその中で作業する
   **セッション自体を `<dir>` で起動し直す（必須）。** hook は書き込み先と cwd・`cd <dir>` から守る対象を決めるので、主 checkout で
   起動したままだと、`cd` を付けない Bash（`gh pr merge` など）と Stop / 経過時間の hook が無人区間を見失う。
2. **機械に候補を出させる**（決めるのは利用者）:
   - `python3 $K/oneshot-spec.py detect` → test / lint / typecheck / build の実コマンドと、runner ごとの実行件数の取り方（`count`）の候補
   - `python3 $K/oneshot-spec.py init` → `oneshot.yaml` の下書き（budget は空・forbid の既定入り）
3. **6 問を聞く**（設計 §2.1。数値の既定値は置かない。答えから導いた値を提示し、利用者が確定する）:

   | # | 聞くこと | 契約のどこへ |
   |---|---|---|
   | 1 | 何ができれば「終わり」か — 人が目で見て判断する言い方を、コマンドで判定できる言い方に置き換える | `dod`（kind: new・`count` 必須） |
   | 2 | 壊してはいけないもの（既存テスト・CI の required check） | `dod`（kind: regression）・`forbid` |
   | 3 | 触ってよい場所 / 触らない場所 | `scope` / `forbid`（契約と gate の設定自身は既定で forbid） |
   | 4 | 画面を触るか（触るなら a11y と l10n のゲート） | `dod`（kind: boundary） |
   | 5 | 新しい判定ごとに、何を壊したら赤になるべきか | `dod[kind=new].mutate`（`python3 $K/oneshot-spec.py mutate-candidates --files <触る予定のファイル>`） |
   | 6 | いつまでに要るか・途中経過を見たいか・失敗が続いたらどうするか・subagent を何回まで使ってよいか | `budget`（`python3 $K/oneshot-spec.py budget --deadline-hours H --fail-tolerance N --watch-every-min M --subagents S` で導出を提示） |

4. **契約を検査する**: `python3 $K/oneshot-preflight.py check` — exit 0 になるまで 3 に戻る（exit 1 = 対話で直す・exit 2 = このモードでは受けられない：level ≥ 2・scope 空）。**3 種（新規 / 回帰 / 境界）・new の `mutate` と `count`・budget 3 値**が揃うまで出口は無い。
5. **人が読む文書を作る**: `python3 $K/oneshot-spec.py spec` → `oneshot/spec.md`。利用者に見せ、**「これで走らせてよい」と明示的に言われたときだけ**次へ進む。ここが最後の人の判断。
6. 契約を commit する: `git add oneshot.yaml oneshot/spec.md && git commit -m "<type>(oneshot): 契約 — <goal>" -- oneshot.yaml oneshot/spec.md`（PR に契約が載り、無人区間の基準点になる）。
   **作業ツリーは clean にしておく**（start は commit 済みの基準点からしか始めない）。spec.md は契約の sha256 を持ち、承認の後に契約を変えたら start が止める（spec を作り直して人に見せ直す）。

## 3. 無人区間に入る — preflight

```bash
python3 $K/oneshot-preflight.py start
```

- exit 0: `oneshot/state.json` ができ、**ここから無人区間**（hook が働き始める・§6 の常設指示が効き始める）。
- exit 1: red-first で**実装前から全部緑**（やることが無い／測定器が壊れている）か、spec.md が無い → Phase −1 に戻って利用者と直す。
- exit 1 には、spec.md と契約が違う（承認の後に契約が変わった）・契約が commit されていない・作業ツリーが clean でない、も入る。
- exit 2: 既定ブランチ / detached HEAD の上・既に走っている・契約が受けられない → 直すのは人。

## 4. 周を回す（無人区間）

各周、この順で進める。**状況のメモは次のツール呼び出しと同じメッセージに書き、テキストだけで手番を終えない**（§6）。

1. **境界を確かめる**: `python3 $K/oneshot-preflight.py boundary` — 0 = 続ける／2 = 予算（attempts / minutes / tokens）を超えた → §5-2 escalate／3 = 人が `oneshot/STOP` を置いた → §5-3 割り込み。
   試行の上限は state の周の数で数える（goal-loop の state を消しても数え直さない）。
2. **初周だけ**: 影響が 3 軸以上なら dev-explorer を並列で（読み取りのみ）。dev-planner の計画を `oneshot/plan.md` に書く（a11y は「N/A」でも書く・worktree に残り PR には載らない）。`python3 $K/oneshot-preflight.py note --phase plan --text "<次の周への注意 1 行>"`。
3. **実装する**（メイン Claude が Generator。実装は subagent に委ねない）。**一発達成を狙わず、前の周の指摘を 1 歩だけ直す。** scope の外・契約・gate の設定には触らない（hook が止める。止められたら案内の代替手順に従う）。
4. **検証する**: dev-tester ∥ dev-reviewer（Checker・読み取りのみ）。subagent のモデルは「設計の余地」で選ぶ — データモデル・状態遷移の判断が要るなら大きいモデル、型どおりの追加・定型の検査なら小さいモデル。
   終わったら数を残す（報告書の Checker 行の出所）: `python3 $K/oneshot-preflight.py note --checker-findings <指摘の総数> --checker-remaining <残> --reviewer-rounds <周> --tester-rounds <周> --subagents <起動の合計>`。
5. **判定する**（完了は exit code だけ。自己申告で「できました」と書かない）:
   ```bash
   bash $K/goal-loop.sh --goal "<oneshot.yaml の goal>" --check "python3 $K/oneshot-preflight.py round" --max <budget.attempts> --state oneshot/.state
   ```
   `round` は dod を全部回し、new の実行件数が 0 か取れなければ赤、forbid / gate / scope 外への変更があれば違反として、結果を state.json に書く。
   生成物（`__pycache__`・build 出力）が ignore されていないと scope 外の違反になる。`.gitignore` は scope の外なので自分で足さず、§5-2 で止まって人に頼む（§5-2b）。
   - exit 1（CONTINUE）→ `note --phase loop --text "<次の周への注意 1 行>"` → 1 へ
   - exit 0（GOAL MET）→ §5-1
   - exit 2（CAP）→ §5-2
6. 進み具合は `python3 $K/oneshot-preflight.py status`（1 行）。draft PR があれば同じ 1 行を `gh pr comment` に出す（人はこれを見ている）。

**state.json に書くのは進捗・未確定・次の周への注意 1 行だけ。決定は書かない**（決定は spec.md・rules・コードコメントへ）。state.json は Write / Edit で書かない（hook が止める）— 更新は `oneshot-preflight.py note` で行う。

## 5. 終わり方

### 5-1. GOAL MET → 測定器が本物か確かめて PR を開く

1. `python3 $K/oneshot-preflight.py mutate` — new の `mutate` を 1 本ずつ当て、**赤になる**ことを確かめて戻す。1 本でも緑のまま・何も変えない・当てられない・判定がタイムアウト → 測定器が壊れている → §5-2（実装を直しても意味がない。所見に書く）。
   **round で緑になった木にしか当てない**（round の後に 1 ファイルでも変えたら round からやり直す）。ignore 対象のファイルは退避の対象外なので、mutate はリポに commit され得るファイルだけを壊す形で書く。
2. **やらなかったことを 1 件以上書く**（空は「見ていない」と同じ）: `note --not-done "<scope 外で必要と分かったもの・仕様の曖昧点の仮置き など>"`。
3. **検証した中身を全部** path-bound commit する（scope 内の変更と `oneshot/plan.md`・`oneshot/state.json` 等の実行時ファイルは exclude 済みで載らない）→ `git push -u origin <branch>`。
   delivered は「PR に載る commit（HEAD）の木 = round で緑になった木」でなければ受けない（検証していない物を載せない・検証した物を置き忘れない）。
4. 報告書: `python3 $K/oneshot-report.py > <scratch>/oneshot-report.md`（exit 0 でなければ足りないものを埋める）→ `gh pr create --title "..." --body-file <scratch>/oneshot-report.md`。**merge しない。**
5. `python3 $K/oneshot-preflight.py note --status delivered --pr <URL>`（直近の round が全部緑・mutation が全部赤・PR あり・やらなかったこと 1 件以上、でなければ拒否される）。

### 5-2. CAP・測定器の不具合・人が要る → escalate

1. 今の差分を commit して push し、`gh pr create --draft` で**未達の dod と最後の差分**を出す（本文は `oneshot-report.py` の出力）。
2. `python3 $K/oneshot-preflight.py note --blocker "<人が要る理由 1 行>" --status escalated` → 止まる。

### 5-2b. 人が直して再開する（`resume`・**人だけが呼ぶ**）

escalate の後、人が契約・gate 設定・scope 外（例: 生成物を `.gitignore` に足す）を直したら、**人が**
契約を変えたなら `oneshot-spec.py spec` で spec を作り直して commit し、自分の端末（Claude Code の外のシェル）で
`python3 $K/oneshot-preflight.py resume --accept <人が承認する scope 外のパス>`（繰り返し可）を実行する。
resume は契約の基準をその commit に移し、**名指しした変更だけ**を以後の round で数えない（名指ししていない scope 外の変更は違反のまま残り、そう表示する）。
止まっている間も、契約・spec・gate 設定・`oneshot/` への Claude の書き込みは hook が止める（直すのは人）。
**Claude のツール経由の resume は hook が止め、道具自身も Claude Code の中（`CLAUDECODE=1`）からの実行を拒否する**（自分で escalate → 柵の外を書き換え → 再開、で無人区間の柵を外せないように）。

### 5-3. 人の割り込み（`oneshot/STOP`・Ctrl-C）

`boundary` が exit 3 を返したら、その周の境界で止まる（kill しない・worktree も途中の commit も捨てない）。
state.json の `phase` を見て `/build` の該当 phase から**対話で**続ける（実装中なら Phase 3、検証中なら Phase 4）。
止まっている間（stopped / escalated）は、対話中でも契約・spec・gate 設定・`oneshot/` を Claude は書けない（hook が止める・直すのは人）。
同じ契約で無人区間に戻るなら人が `resume`、やめるなら人が `oneshot/state.json` を片付ける（止まった run の上で `start` し直すことはできない）。

## 6. 無人区間の常設指示（`start` が exit 0 を返してから効く。Phase −1 では効かない）

> 利用者（あなたが作業している相手）からの常設の指示。手番の終え方について。ツール呼び出しを含まないメッセージを送ると手番が終わり、続けてと言われるまで作業は止まる。利用者は、頼んだ作業が残っているのに手番を終える次の 4 つの型を見てきたが、どれも望んでいない。一つ目: やったことの長い要約を書き、次の手順を予告して、ツール呼び出しの無いまま終わる（次が始まらない）。二つ目: 「よろしければ続けます」のように、利用者が答えるつもりのない返事を待って止まる。三つ目: 自分の説明でもどれも残りの作業を止めていないのに、利用者への判断事項を並べて止まる。四つ目: 手番が長くなった・区切りがついた、という理由で報告の頃合いだと判断する。進捗の報告や未決の判断への推奨は歓迎する。ただし次のツール呼び出しと同じメッセージに書き、利用者の答えに依存しない作業を続けること。利用者に方向転換を促したり、待つと申し出たりしている自分に気づいたら、それを消して次のことをする。利用者が望む停止は、利用者なしでは何も進まないとき、または進めない原因が意図的にあなたから保護されているときだけで、そのときは §5-2 escalate（blocker を 1 行記録・status=escalated）で止まる。この指示は、危険な操作・取り返しのつかない操作の前の確認を上書きしない。

出典は設計 §3.1・§7.2（Anthropic「Prompting Claude Opus 5.5」Unattended agentic runs）。この指示でツール呼び出しと出力トークンがやや増える（`budget.tokens` を導くときに見込む）。

## 7. やらないこと

- merge・deploy・公開・release・価格・顧客リポへの write（hook も止める。要るなら escalate）
- 契約（`oneshot.yaml`・`oneshot/spec.md`）・gate の設定（baseline・除外リスト・floor）を変えて通す — `round` が違反にする
- 仕様の曖昧さを推測で埋める — Phase −1 なら聞く。無人区間なら「やらなかったこと」に仮置きを書く
- 「テストが通りました」と自己申告で完了にする — 完了は `round` の exit 0 と `mutate` の exit 0 だけ
