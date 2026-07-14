---
description: プロジェクトの現況を live 実測して次アクションを決める（6 phase）— 同期→スタック検出→read-only 実測→状態表→次アクション→/build 連携。文書=plan/live=state・自己申告禁止（probe 無き状態行は出さない）・probe 失敗は 0 でなく ❓・project-standards 拡張に対応。
allowed-tools: Read, Glob, Grep, Bash, Agent
---

# /pulse — Live-State Status & Next Actions

プロジェクトの「今の実態」を deterministic な read-only 実測で確定し、状態表と次アクションに落とす。中核の掟は **文書=plan / live=state** — README・過去ログ・自分の記憶・memory はすべて *意図* に過ぎず、実測 probe だけが *状態* を語る。**probe 行の裏付けが無い状態表現（"完了" "merged" "deployed" "0 件" "passing" "blocked"）は書かない**（自己申告禁止）。probe が失敗したら **`0` ではなく `❓ unknown`** と書く（"空出力 ≠ ゼロ"）。

pre-check スクリプトが **Verifier**、メイン Claude が **Generator**。レポートは Verifier 出力の *rendering* に徹する。`/pulse` は **一切 mutate しない**（sync すら fetch のみ）。`/build`（作る）と対の `/pulse`（測る）。

---

## Phase 1: 同期（読み取りのみ）

**常に実行**。`git fetch --all --prune` で remote refs だけ更新する（**working tree は触らない**）。fetch 失敗（offline 等）は abort せず `❓` を出して継続。※ Phase 2 のスクリプトが冒頭で fetch を行うため、通常はスクリプトに任せてよい。

## Phase 2: live 実測（pre-check スクリプト = Verifier）

**常に実行**。stack / PR host / CI を自動検出し、read-only probe を回す。**このスクリプトの stdout が唯一の ground truth**。

```
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/pulse-precheck.sh"
```

> plugin install 時は `CLAUDE_PLUGIN_ROOT` が自動で入る。source から試すときは
> `CLAUDE_PLUGIN_ROOT=/path/to/willink-claude-kit` を渡すか、リポジトリに `.claude/hooks/`
> 等へコピーしたパスを指定する（見つからなければ「❓ precheck script not found」を出して先に進まない）。

probe: git ahead/behind vs upstream ・ 未 commit/未追跡 WIP ・ open PR + review 状態（gh/glab）・ HEAD の CI 結論 ・ 最新 tag..HEAD（merged≠deployed）・ cheap check（cost-gate）・ merged/stale ブランチ ・ TODO/FIXME 密度 ・ 依存 audit（cost-gate）・ prod fingerprint（config）・ state-doc 鮮度。各 probe は **1 回 retry → 失敗は `❓`**、`0` と混同しない。config（`.claude/pulse.conf` または env）は任意で、無くても origin+stack から core は必ず出る。

## Phase 3: 状態表 synthesis（Generator）

スクリプト出力を 1 行/ワークストリームの状態表に render する。**probe 行が無い項目は表に出さない**（＝ hallucination）。`project-standards`（`.claude/skills/project-standards/` があれば preload）を読み、このプロジェクトで「何が重要か」に沿って並べ替える（無ければ core 順で出す）。

| 領域 | signal | 実測値 | 直近の変化 | 律速（誰の番か） |
|---|---|---|---|---|
| … | 🟢/🟡/🔴/❓ | probe 由来 | … | … |

各行に **実測時刻**を刻む（スクリプト冒頭の `measured …` を引用）。変化の無い行は「no change」1 行に畳む（行は残す・再ナレーションしない）。

## Phase 4: 深掘り（dev-explorer ∥ dev-reviewer）— 任意

**起動条件**: 🔴 が **3 領域以上独立**のとき `dev-explorer` を並列（原因調査）。未 commit の WIP diff が **>50 行**で妥当性判断が要るとき `dev-reviewer`（読取専用レビュー）。単一 🔴 / 小 WIP は skip（直接 Read/Grep）。

```
dev-explorer × N（read-only・最大 3 並列）: 🔴 領域ごとに原因を scoped 調査
dev-reviewer × 1（read-only）: WIP diff を PASS/CONDITIONAL/FAIL 判定
```

両 agent とも read-only。実装・修正はしない（Generator-Verifier 構造を保つ・dev-standards + project-standards を preload・**追加 agent は定義しない**＝既存 4 本の再利用のみ）。

## Phase 5: 次アクション決定 + owner routing

実測状態から **上限 5 件**の優先アクションを出す。**各アクションは根拠 probe 行を必ず引用**（probe 行が無い → アクションを作らない＝busywork 禁止）。各項目に owner + 見積を付す。

owner routing（可逆性 × 外部到達で決める・自己申告禁止）:
- **自分で今やる**: 可逆 かつ 内部完結 かつ 非金銭非法的 — commit / feature branch push / draft PR / test 実行 / 冪等 re-deploy / **measured MERGED** なブランチ削除
- **人に渡す**: 不可逆 or 外部到達 or 金銭/法的 or 判断・レビュー gate そのもの — 1-click 検証リンク + 「なぜ人が要るか」1 行を添える
- 可逆性が不明なら **dry-run で実測**してから振り分ける（未解決の軸名を書けた時だけ escalate）

**human-decision バケットは空でも必ず出す**（`人待ち: なし ✅`）— 忘れた承認 gate を "沈黙" ではなく "可視の不在" にする。PR 番号を転記する前に `gh pr view <N> --json state,mergedAt` を再実測（"measured once" ≠ "measured now"）。

## Phase 6: /build 連携

先頭の自己実行アクションがコード変更なら、その scope をそのまま `/build` に渡す（探索→計画→実装→並列検証→commit）。人待ち項目は渡さず Phase 5 のバケットに残す。

```
/build <Phase 5 で選んだ最優先のコード変更 scope>
```

---

## subagent skip 判断早見表

| 状況 | Phase 4 深掘り | 次アクション起点 |
|---|---|---|
| 全 🟢 | skip | 軽い掃除（stale ブランチ削除）or 何もしない |
| 単一 🔴（1 領域） | skip（直接 Read/Grep） | 該当 fix → /build |
| 🔴 が 3 領域以上 | dev-explorer 並列 | 原因確定後 /build |
| 大きめ WIP diff（>50 行） | dev-reviewer | commit or 破棄判断 |
| merged≠deployed | skip | deploy 起動 → prod 再 probe |
| stale ブランチのみ | skip | measured MERGED を削除（自分で） |

---

## 優先度（高い順・"燃えてる/出したのに未検証" が "単に pending" に勝つ）

| 順位 | トリガ probe | 起点アクション | owner |
|---|---|---|---|
| 1 | prod 🔴（fingerprint 欠落 / deploy 失敗） | live regression を止める | 状況次第 |
| 2 | default branch の CI 🔴 | 修正 or escalate | 自分/人 |
| 3 | tag..HEAD >0 / merged 未 deploy | deploy 起動→prod 再 probe | 自分（冪等）/人 |
| 4 | 未 commit/未 push WIP | commit/push で単機リスク解消 | 自分 |
| 5 | 緑+approved の未 merge PR | merge or hand off | 自分/人 |
| 6 | stale MERGED ブランチ | 削除（可逆・内部） | 自分 |
| 7 | TODO/FIXME 増・新規 advisory | 起票（落とさない） | 自分 |

---

## 失敗モード対策（公式ブログ + 実運用の傷跡準拠）

- **自己申告（緑ダッシュボードの捏造）**: probe 行の無い状態表現を出さない（Generator は Verifier 出力の render のみ）
- **fail-to-zero**: probe 失敗は `❓ unknown`・絶対 `0` と書かない（断続 gh 401 を "0 open PR" と誤読した near-miss 対策）
- **merged≠deployed**: `tag..HEAD` と prod fingerprint で drift を明示検出
- **green-while-broken**: HTTP 200 だけで OK にしない・expected substring を assert（🔴 は最優先）
- **Early victory**: 重いフルスイートは status では回さない（cost-gate → `⏭ not run`）
- **Subagent 爆発**: 深掘りは 🔴 かつ 3 軸独立の時のみ・agent は既存 4 本に厳選（追加禁止）
- **stale doc を信じる**: bot が毎日書く doc は新しく見えても中身は古い（freshness ≠ truth）

---

## 関連

- `agents/` — dev-explorer / dev-reviewer（Phase 4 で再利用・追加なし）
- `scripts/pulse-precheck.sh` — read-only live-state Verifier（本コマンドの ground truth・`scripts/test/test_pulse_precheck.sh` で自己テスト）
- `skills/dev-standards/` — 共通開発標準（各 agent が preload）
- `.claude/skills/project-standards/` — プロジェクト固有の「何が重要か」（任意・無くても動作）
- `commands/build.md` — Phase 6 の連携先
- `docs/harness-profile.md` — 決定論ゲートの ladder（/pulse は H3 の read-only 実測層）
- `docs/failure-modes.md` — 失敗モード詳細
