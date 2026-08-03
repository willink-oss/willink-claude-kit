---
name: codex-imagegen
description: Generate images by delegating to the Codex CLI's built-in image_gen tool, then verify the saved file deterministically (magic bytes + real dimensions) before it is used. Runs on a ChatGPT subscription — no image API key required. Triggers: 画像生成, 画像を作る, image generation, generate image, アイコン生成, バナー生成, OGP 画像, サムネイル, illustration, codex image, imagegen, ビジュアル作成
allowed-tools: Bash, Read, Glob, Grep
---

# codex-imagegen — Codex CLI 経由の画像生成 + 決定論検品

> 実装: `scripts/codex-imagegen.sh`
> **生成（非決定的・LLM）と 検品（決定論・スクリプト）を分離する**という ADR-019「自己申告禁止」の画像版。
> エージェントの「画像を作りました」を成果の根拠にしない。**保存された実ファイル**だけが根拠。

## いつ使うか

- アイコン / バナー / OGP 画像 / サムネイル / 記事挿絵など、**ラスタ画像を新規生成**したいとき
- 参照画像を渡して**同じ世界観の別カット**を作りたいとき（`--ref`）

## いつ使わないか

- **図表・チャート・グラフ** → 画像生成ではなくデータ可視化（`dataviz` 等）を使う。数値を LLM に描かせない
- **ロゴの確定版・ブランド資産の刷新** → 生成は可だが**採用は Level 3**（ブランド）。本 skill は候補出しまで
- **既存画像のリサイズ・変換だけ** → `sips` / ImageMagick で足りる。LLM を経由しない

## 前提

- `codex` CLI がインストール済みで **ログイン済み**（`codex login status`）
- 認証は **ChatGPT サブスクリプション**で足りる。**画像 API キーは不要**（= 追加課金なし）
- 内部ツール名は `image_gen__imagegen`（Codex 側の組み込み。MCP や plugin の追加登録は不要）

## 使い方

```bash
scripts/codex-imagegen.sh \
  --prompt "濃紺の背景に、白い細線で描かれた幾何学パターン。文字は入れない。" \
  --aspect "16:9" \
  --out assets/img/banner.png
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--prompt <text>` | （必須） | 生成指示 |
| `--out <path>` | （必須） | 出力パス（`.png` / `.jpg`） |
| `--aspect <spec>` | — | `1:1` / `16:9` / `4:5` 等。**実測で概ね反映される**が厳密保証はしない |
| `--ref <file>` | — | 参照画像（繰り返し可） |
| `--attempts <n>` | 2 | 検品失敗時の再試行回数 |
| `--min-bytes <n>` | 10000 | 空 / 切断ファイルの検知しきい値 |
| `--model <name>` | `gpt-5.6-sol` | Codex のモデル |
| `--effort <level>` | `low` | reasoning effort |
| `--timeout <sec>` | 300 | 1 回あたり上限（`gtimeout`/`timeout` がある時のみ強制） |

**Exit code**: `0` = 生成 + 検品 pass / `1` = 検品 fail（試行使い切り）/ `2` = 引数誤り・`codex` 不在

## 検品が保証すること・しないこと

スクリプトが決定論的に検査するのは**実ファイル**のみ:

- ファイルが存在する（`codex` の自己申告ではなく `--out` のパスを見る）
- マジックバイトが PNG / JPEG として妥当（PNG は `IHDR` の存在まで）
- 実寸が取得でき、極端に小さくない
- バイト数が `--min-bytes` 以上

**保証しないのは「絵の内容が意図通りか」**。exit 0 は「妥当な画像ファイルである」までしか言っていない。
→ **対外公開の前に必ず人間（または別エージェントの Verifier）が実ファイルを目視する。**

## Gotchas（実測で踏んだもの）

- **プレビューと実ファイルは別物** — 2026-07-31 に別の生成系で、プレビューは正しいのに DL したフルサイズが違うという事例があった。**検品は必ず保存済みの実ファイルに対して行う**（本スクリプトはそう作ってある）
- **exit 0 ≠ 正しい絵** — 「ステータスが通った」で完了報告しない。API 検証で「200 ≠ 正しい」と同じ罠
- **ブランド色を記憶で決めない** — 「あの製品は青」のような記憶は外れる（実例: PULSE の色だと思っていたものが実際は fit-ai のブランド色だった）。**デザインシステムの実値を引いてからプロンプトに書く**
- **reasoning effort を上げても絵は良くならない** — `--effort low` が既定。上げるとコストと時間だけ増える
- **空配列の展開に注意**（スクリプト保守者向け）— `"${arr[@]:-}"` は空文字を 1 個渡してしまい `codex exec` が usage エラーになる。`[ ${#arr[@]} -gt 0 ] && CMD+=("${arr[@]}")` で回避済み
- **macOS に `timeout(1)` は無い** — `gtimeout`（coreutils）が無ければ上限秒は強制されず、警告だけ出る
- **生成物は `~/.codex/generated_images/<session>/` にも残る** — 秘匿性のある素材を生成した場合はそちらの残骸も意識する

## 承認レベル

- **生成そのもの = Level 1**（内部・可逆・非金銭）。サブスク内で完結し追加課金は発生しない
- **対外公開 = Level 2 以上**。SNS / サイト / 配布物に載せる判断は本 skill の外
- **ブランド資産の確定 = Level 3**（ブランド）
