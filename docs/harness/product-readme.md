# proof-harness

**エージェントの「できました」を、終了コードで潰すためのゲート束。**

Claude Code 向けの skill 26 本 + 決定論エンジン 13 本（約 6,900 行）。
Python3 標準ライブラリと POSIX shell のみで動き、**ネットワークを使いません**。

```bash
./verify.sh
```

このコマンドが exit 0 を返すことが、この製品の唯一の宣伝文句です。

```
検査 17 件: 合格 17 / 不合格 0
✅ PASS — skill 26 本 / engine 13 本が宣伝どおり動作
```

---

## なぜ必要か

エージェントは、自分の仕事を自分で採点すると必ず甘くなります。
プロンプトの改善では解けません。**判断する主体と作業した主体が同じ**だからです。

実測例: あるゴール到達型ループで、エージェントの自己判定は「90% 以上達成」でした。
決定論ゲートで測ると **55.9%**（テストがコンパイルすら通っていない）。
ゲートを噛ませ直した結果、98.3% で停止しました。

このハーネスは、その「外側の判定」を 26 個の観点で用意したものです。

---

## 何が入っているか

### ハルシネーション・裏取り

| skill | 何をするか |
|---|---|
| `hallucination-rate-meter` | 文書の主張を出典照合し、裏付けの無い主張の割合を採点して閾値超を落とす |
| `knowledge-citation-coverage` | 主張に対する出典 URL の充足率を採点 |
| `review-finding-repro` | 同じ差分を複数回レビューした時の指摘一致率（Jaccard）でレビューの安定性を測る |
| `commit-msg-quality-score` | 「〜を更新」で終わる空虚なコミットを決定論的に検出 |

### 評価・回帰

| skill | 何をするか |
|---|---|
| `eval-dataset-build` | golden データセットを schema 検証つきで構築 |
| `eval-regression-guard` | baseline との差分で回帰を検知しブロック |
| `prompt-ab-eval` | プロンプト A/B の勝者とマージンを決定論判定 |
| `task-success-rate-track` | タスク成功率を追跡し停滞を検出 |

### ハーネス自身の健全性

| skill | 何をするか |
|---|---|
| `harness-kpi-ledger` | ルール残数・フック数・CI required check 数を計測し退行を検知 |
| `rule-promotion-extract` / `rule-promote-audit` | 自然言語ルールのうち機械検査に落とせるものを抽出 |
| `rule-dedup-scan` / `trigger-vocab-dedup` | ルール重複と skill のトリガー語彙衝突を検出 |
| `gate-forge` | 再発したミスの記述から fail-closed フックの scaffold を生成（block/pass 両ケース付き） |
| `ci-required-check-installer` | required status check の未導入を読取専用で監査 |

### エージェント運用の観測

| skill | 何をするか |
|---|---|
| `context-bloat-tracker` | セッション別の入力量から肥大したセッションを検出 |
| `session-length-compact-audit` | 要約のタイミングを実データから点検 |
| `subagent-usage-analyzer` | サブエージェントの過剰起動を検出 |
| `retry-failure-classifier` | 失敗・リトライの多いツールを分類 |

### 安全・秘密

| skill | 何をするか |
|---|---|
| `destructive-env-guard` | 破壊/環境変更コマンドがフックで塞がれているかを読取専用で監査 |
| `secret-scan-hardening` | secret 検出パターンの穴を検査 |

### 知識・記憶の腐敗

| skill | 何をするか |
|---|---|
| `knowledge-index-guard` | 索引が実体と一致し broken link 0 であることを保証 |
| `knowledge-dedup-scan` | 近似重複クラスタを検出し統合候補を提示 |
| `memory-distill` | 索引と実体を突合し孤児・リンク切れ・重複を検出 |
| `mistake-rule-distill` | ミスログのうちルール化されていないものを抽出 |

その他: `codex-spark-delegate`（別 usage lane への委譲）

---

## 使い方

```bash
# 1. 自分自身の検査に通ることを確認（買った直後にまずこれ）
./verify.sh

# 2. 対象リポジトリへ配線（何をするかだけ先に見る）
./install.sh --target /path/to/your/repo --dry-run
./install.sh --target /path/to/your/repo

# 3. 対象リポジトリで動かす
cd /path/to/your/repo
python3 .claude/willink-kit/engines/harness-lint.py skill-desc
```

`install.sh` は **hooks 登録・CI 設定・settings.json の編集をしません**。
これらは「導入すると作業が止まるようになる」変更なので、人間が判断して配線します
（[`docs/harness/wiring.md`](docs/harness/wiring.md)）。

---

## 前提

- Python 3.8+（標準ライブラリのみ）
- bash / POSIX shell（macOS の BSD 系コマンドで動作確認済み・`grep -P` 不使用）
- git
- **ネットワーク不要**・API キー不要・外部サービス登録不要

---

## 読む順番

1. [`docs/oss-vs-pro.md`](docs/oss-vs-pro.md) — **無料 OSS 版との違いと、正直な開示**
2. [`docs/harness/incidents.md`](docs/harness/incidents.md) — 各ゲートを生んだ事故の台帳
3. [`docs/harness/principles.md`](docs/harness/principles.md) — 3 つの原則
4. [`docs/harness/practices.md`](docs/harness/practices.md) — ゲートの外側で効く運用
5. [`docs/harness/wiring.md`](docs/harness/wiring.md) — フック / CI への配線

**先に [`oss-vs-pro.md`](docs/oss-vs-pro.md) を読んでください。**
同種のものが無料で手に入ること、弊社自身が 17 本を MIT で無料公開していることを、
そこに書いています。

---

## ライセンス

商用ライセンス。[`LICENSE.md`](LICENSE.md) / 第三者コンポーネント: [`NOTICE.md`](NOTICE.md)

© 2026 合同会社 i-Willink
