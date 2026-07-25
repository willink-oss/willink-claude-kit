---
name: a11y-static-gate
description: Read-only deterministic gate that fails a build when an interactive element has no accessible name / role / state — across Flutter (.dart), React (.tsx/.jsx) and PHP/HTML templates. Structure-aware (ancestor chain, not line windows), with a baseline + ratchet so a legacy repo can adopt it without a rewrite, and an anti-gaming refusal to grow the baseline. Triggers: a11y gate, accessibility gate, アクセシビリティ ゲート, semantics 欠落, accessible name, button role, alt 欠落, 記号ラベル, a11y baseline, ratchet.
---

# a11y-static-gate

`${CLAUDE_PLUGIN_ROOT:-.}/scripts/a11y-static-check.py` は、コードツリーを走査して
**「支援技術から操作できない UI 要素」** を決定論的に検出する。

「ちゃんとラベルを付けて」という指示は確率的だが、exit code は確率的でない。

## 何を見るか

構造（括弧/タグのスタックと祖先チェーン）を解析するので、「この tappable を包む祖先に
ラベルがあるか」に正しく答えられる。行ウィンドウの grep はこれを両方向に間違える。

| rule | severity | 検出内容 |
|---|---|---|
| `A11Y-FLUTTER-NAME` | error | `GestureDetector`/`InkWell`/`IconButton` 等に tap があるが名前が無い |
| `A11Y-FLUTTER-ROLE` | error | ラベルはあるが `Semantics(button: true)` が無い自作ボタン |
| `A11Y-FLUTTER-SYMBOL-LABEL` | error | ラベルが記号のみ（`<` `×` `•••`）＝記号名で読み上げられる |
| `A11Y-FLUTTER-STATE` | warn | 条件付きコールバックなのに `Semantics(enabled:)` が無い |
| `A11Y-FLUTTER-FIELD-NAME` | error | `TextField` に名前が無い（`hintText: ''` は名前ではない） |
| `A11Y-FLUTTER-TOGGLE-NAME` | error | `Switch`/`Checkbox` に名前が無い |
| `A11Y-FLUTTER-IMAGE-LABEL` | warn | `Image` に `semanticLabel` も `excludeFromSemantics` も無い |
| `A11Y-WEB-INTERACTIVE-DIV` | error | `div`/`span` に click があるが role/tabIndex/キーボード処理が無い |
| `A11Y-WEB-CONTROL-NAME` | error | `button`/`a` に名前が無い（アイコンのみ） |
| `A11Y-WEB-SYMBOL-LABEL` | error | `button`/`a` のラベルが記号のみ |
| `A11Y-WEB-IMG-ALT` / `A11Y-MARKUP-IMG-ALT` | error | `img` に `alt` が無い（`alt=""` は装飾として許容） |
| `A11Y-WEB-FIELD-NAME` / `A11Y-MARKUP-FIELD-NAME` | error | 入力要素に label 関連付けも `aria-label` も無い |
| `A11Y-WEB-FIELD-LABEL-UNVERIFIED` | warn | `id` はあるが対応する `<label for>` を静的に確認できない |
| `A11Y-*-VIEWPORT-ZOOM` | error | `user-scalable=no` / `maximum-scale<2`（拡大禁止） |
| `A11Y-*-POSITIVE-TABINDEX` | error | 正の `tabindex`（タブ順が DOM 順から乖離） |
| `A11Y-PROJ-SCALE-TEST` | warn | 最大文字サイズを再現する自動テストが無い（Flutter repo） |
| `A11Y-PROJ-WEB-LINT` / `-SUBSET` | warn | a11y linter 未設定 / Next.js 既定の部分適用のみ |

## Boundary（read-only）

- **検出のみ**。`Semantics` を挿入したりコードを書き換えたりしない。修正は人（と PR）の仕事
- **自分を CI に配線しない**。required status check / branch protection 化は self-lockout 側のリスクを持つ変更なので、人が意図して行う（→ [`examples/ci/a11y-gate-pattern.md`](../../examples/ci/a11y-gate-pattern.md)）
- **gh / aws / git を呼ばない**。ローカルのファイル読取だけ・python3 stdlib のみ
- **静的解析はアクセシビリティを証明しない**。緑は「機械が見つけられる命名/役割の欠陥が無い」だけ。読み上げでの通し操作と拡大時のレイアウトは `a11y-standards` の自動テストと実機確認が担う

## 使い方

```bash
# 単発（新規プロジェクト・違反 0 が前提）
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/a11y-static-check.py" --root .

# 既存 repo（違反が既にある）: 現状を凍結して以後の新規違反だけ落とす
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/a11y-static-check.py" --root . \
    --baseline .a11y-baseline.json --update-baseline   # 初回のみ
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/a11y-static-check.py" --root . \
    --baseline .a11y-baseline.json                     # 以後（CI）
```

主なオプション: `--strict`（warn も落とす）/ `--format json` / `--rules <ID,...>` /
`--exclude <substr>`（repeatable）/ `--no-project-checks`。

**exit code**: `0` 新規違反なし / `1` 新規違反あり / `2` 引数・baseline 読取エラー
（**baseline を指定して不在なら 2** — 「既知ゼロ・新規ゼロ」に倒さない）/
`3` **UNKNOWN = 走査対象 0 件**（クリーンではなく `--root`/`--exclude` の誤り）。

出力は必ず**分母**を伴う（走査ファイル数・拡張子別・検査した操作要素数・rule 別件数・
件数の多いファイル上位）。「0 件」だけを出して走査 0 件と区別できない状態にしない。

## baseline と ratchet（レガシー導入の要）

- fingerprint は **内容ベース**（rule + file + 正規化スニペット + 出現順）。行がずれても
  新規違反として再発火しない
- `--update-baseline` は**件数が増える更新を拒否**する。増やすには
  `--allow-baseline-growth` が必要で、その痕跡が diff と PR に残る
  （`coverage-floor-lock` の「floor を下げる diff 自体が違反」と同じ規律）
- 修正後に `--update-baseline` で fixed エントリを刈る。`counts.error` は単調減少が期待値

## 誤検出への対処（除外より抑制を選ぶ）

```dart
// a11y-ignore: 装飾のヒット領域で、同じ操作を上のボタンが提供している
GestureDetector(onTap: noop, child: const Icon(Icons.circle))
```

理由付きの `a11y-ignore: <理由>` のみが抑制する。**理由の無い裸の `a11y-ignore` は抑制せず、
件数として報告される**。ファイル単位の `--exclude` で黙らせるより、1 行の理由を残す。

## Deterministic --check（self-test）

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/a11y-static-check.py" --self-test
```

hermetic（`mktemp` の fixture のみ・repo に何も書かない）。各スタックの検出/非検出、
ratchet、baseline 成長拒否、走査 0 件 = UNKNOWN、抑制 pragma、そして**実 repo で見つかった
偽陽性の再発ロック**（`<label>` の暗黙関連付け・テンプレートリテラル内の JSX コード例・
monorepo の lint 設定・build 用 package.json を持つ PHP テーマ）を検査する。
期待値を 1 つ変えれば FAIL する（成功のハードコードなし）。

## Related

- 設計標準（何を守るか）: [`a11y-standards`](../a11y-standards/)
- 実行時テストの雛形: [`examples/a11y/flutter/a11y_smoke_test.dart`](../../examples/a11y/flutter/a11y_smoke_test.dart) / [`examples/a11y/web/eslint-a11y.config.md`](../../examples/a11y/web/eslint-a11y.config.md)
- CI 配線と ratchet 運用: [`examples/ci/a11y-gate-pattern.md`](../../examples/ci/a11y-gate-pattern.md)
- 同じ族のゲート: [`coverage-floor-lock`](../coverage-floor-lock/)（下限ロック）・[`architecture-parity-gate`](../architecture-parity-gate/)（構造違反）
