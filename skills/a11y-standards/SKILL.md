---
name: a11y-standards
description: UI を作る/変える全変更に適用するアクセシビリティ標準。accessible name / role / state の三点セット + 文字拡大 + コントラスト + ターゲットサイズ + キーボードを、Flutter / React(Next.js) / WordPress(PHP) のイディオムと検証手段（決定論ゲート・自動テスト・人手）に落とす。Triggers: accessibility, a11y, アクセシビリティ, screen reader, VoiceOver, TalkBack, semantics, ARIA, alt text, dynamic type, 文字サイズ, contrast, keyboard navigation, tap target, WCAG.
---

# a11y standards — 後付けしない前提の設計標準

アクセシビリティは**実装後に監査で足すものではなく、UI を設計する時点で決めるもの**。後付けは「操作要素が全部無名」のような構造的欠陥になり、修正が全画面に散る。

このスキルは UI に触る全変更に適用する。`dev-standards` から参照され、`/build` の Phase 2（計画）と Phase 4（検証）で使う。

---

## 1. 最小契約（10 行・スタック非依存）

新規/変更した UI がこれを満たすまで実装完了としない。

1. **name** — 操作要素はすべて意味のある accessible name を持つ（WCAG 2.2 SC 4.1.2 / Level A）
2. **role** — 押せるものは「ボタン」等の役割を宣言する。見た目だけのボタンは支援技術にはボタンではない
3. **state** — 無効・選択中・開閉・トグルは状態として公開する（見た目の色だけで表さない）
4. **記号をラベルにしない** — `<` `×` `…` `+` は記号名で読み上げられる（`<` は「小なり」）。語を与え、記号は読み上げから外す
5. **文字拡大 200%（iOS は AX5 ≒ 3.12x）でレイアウトが壊れない** — 固定高さに文字を詰めない（SC 1.4.4 / 1.4.10）
6. **コントラスト** — 本文 4.5:1・大きい文字 3:1（18pt / bold 14pt 以上）・UI 部品と状態表示 3:1（SC 1.4.3 / 1.4.11）
7. **ターゲットサイズ** — 最小 24×24 CSS px（SC 2.5.8）。モバイルは実務上 44×44（iOS）/ 48×48（Android・Flutter 公式チェックリスト）を採る
8. **キーボードだけで完結する**（Web/デスクトップ）— フォーカスが見え（SC 2.4.7）、順序が視覚順と一致し、トラップしない
9. **色だけで情報を伝えない**（SC 1.4.1）／**モーションは `prefers-reduced-motion` で止める**（SC 2.3.3）
10. **主要導線 1 本は読み上げだけで通せる** — 「ログイン → 主要操作 → 完了」を音声のみで到達できること。ここが受入条件

> **静的ゲートが緑 ≠ アクセシブル**。ゲートは「機械が見つけられる欠陥が無い」ことしか言わない。10 は人手でしか確認できない。

### 対外的な表記の線引き（重要）

自動検出でカバーできるのは違反の一部（Deque の調査で issue 件数ベース約 57%）。
**「WCAG 2.2 AA 準拠」「アクセシブル」と書いてよいのは人手監査を経た場合だけ**で、
受託契約・LP・ストア申告に書くのは Level 3（社長承認）。ゲートが言えるのは
**「機械判定可能な違反ゼロ（走査 N 件）」**まで。この線を越えた表記は法的リスクになる。
出典: https://www.deque.com/blog/automated-testing-study-identifies-57-percent-of-digital-accessibility-issues/ ・ https://www.w3.org/WAI/test-evaluate/

---

## 2. 設計時に決めること（Phase 2 で必ず書く）

UI を追加/変更する計画には、要素ごとに次を明記する。決めずに実装に入らない。

| 決めること | 書き方の例 |
|---|---|
| accessible name | 「日記を開く」（表示は本のアイコンのみ） |
| role | button / link / textField / header |
| state | enabled: `_canSend` / toggled: `notifyOn` |
| 拡大時の振る舞い | 高さは内容に追従（固定 48 は使わない）・2 行超は省略せず折り返す |
| 読み上げ順 | ヘッダ → 本文 → 操作。Stack で視覚順とずれる場合は `sortKey` |

UI を触らない変更では「a11y: N/A（UI 非変更）」と明示する。空欄は「検討していない」と同じ。

---

## 3. スタック別イディオム（正解と典型的な誤り）

### Flutter

```dart
// ❌ 名前も役割も無い（読み上げでは無名の要素・「ボタン」と言われない）
GestureDetector(onTap: openDiary, child: const Icon(Icons.book))

// ❌ 記号がそのままラベル（VoiceOver は「小なり」と読む）
GestureDetector(onTap: pop, child: const Text('<'))

// ❌ 文字はあるが role/state が無い（「ボタン」でも「無効」でもないと伝わる）
GestureDetector(onTap: enabled ? onTap : null, child: Text(label))

// ✅ name + role + state を 1 ノードに畳み、装飾の記号は読み上げから外す
Semantics(
  button: true,
  enabled: enabled,
  label: '戻る',
  child: ExcludeSemantics(
    child: GestureDetector(
      onTap: enabled ? pop : null,
      child: const SizedBox(width: 48, height: 48, child: Center(child: Text('<'))),
    ),
  ),
)

// ✅ 入力欄は必ず名前を持たせる（hintText: '' は名前ではない）
TextField(decoration: const InputDecoration(labelText: 'メッセージ'))

// ✅ アイコンボタンは tooltip が name になる（engine が accessibilityLabel に昇格させる）
IconButton(tooltip: '共有', onPressed: share, icon: const Icon(Icons.share))
```

- **共通ボタン Widget を 1 つ用意し、生の `GestureDetector` を操作要素に使わない**。ラッパー 1 ファイルの修正が全画面に効く（逆に、ラッパーが無名だと全画面が無名になる）
- 文字拡大のグローバル方針は `MediaQuery.withClampedTextScaling(maxScaleFactor: 2.0)` を root に 1 箇所。
  **2.0 未満のクランプは WCAG 1.4.4（200% まで利用可能）違反**であり、Apple の「Larger Text」申告も満たせない。
  さらに**クランプは緩和策で修正ではない**（実測: 固定高さの箱は 1.6x にクランプしても 16px 溢れた）
- `textScaleFactor` 系 API は deprecated。`MediaQuery.textScalerOf(context).scale(fontSize)` を使う

### React / Next.js

```tsx
// ❌ div にクリック（キーボード・支援技術から操作不能）
<div onClick={save}>保存</div>
// ❌ アイコンのみのボタン / alt 無し画像 / 記号ラベル
<button onClick={close}><XIcon /></button>
<img src="/hero.png" />
<a href="/next">×</a>

// ✅ 素の HTML 要素に戻すのが最短の正解
<button type="button" onClick={save}>保存</button>
<button type="button" aria-label="閉じる" onClick={close}><XIcon aria-hidden="true" /></button>
<img src="/hero.png" alt="海に沈む夕日" />   {/* 装飾なら alt="" を明示 */}
<label htmlFor="q">検索</label><input id="q" type="search" />
```

- `role`/`tabIndex`/`onKeyDown` を手で足すのは、素の `button`/`a` が使えない時だけ
- placeholder はラベルの代替にならない（入力すると消える）
- ダークモードは**別テーマとして**コントラストを検証する（片側だけ落ちるのが典型）

### WordPress / PHP

```php
<?php // ❌ alt 無し・空リンク・ラベル無しフォーム ?>
<img src="<?php echo esc_url( $url ); ?>">
<a href="#"></a>
<input type="search" name="s">

<?php // ✅ ?>
<img src="<?php echo esc_url( $url ); ?>" alt="<?php echo esc_attr( $alt ); ?>">
<a href="<?php echo esc_url( $link ); ?>"><?php echo esc_html( $title ); ?></a>
<label for="s">サイト内検索</label><input type="search" id="s" name="s">
```

- **skip link**（最初の focusable・focus で可視化）／landmark（`<main>` `<nav>`）／`<h1>` は 1 ページ 1 つ・見出しレベルを飛ばさない
- viewport で拡大を禁止しない（`user-scalable=no` / `maximum-scale<2` は 1 行で全ページを壊す）
- 正の `tabindex` を使わない（0 と -1 のみ）
- 本文中リンクは下線を付ける（色だけの区別は不可）。`read more` のような文脈依存リンクテキストを避ける
- WordPress.org の `accessibility-ready` タグを謳う場合は 18 要件 + テーマルートの `accessibility.txt` が要件（→ `docs/a11y-guide.md`）

---

## 4. 検証の三段（下に行くほど強いが自動化できない）

| 段 | 何を保証するか | 手段 |
|---|---|---|
| **静的ゲート**（毎コミット） | name/role/state・記号ラベル・alt・拡大禁止・正 tabindex の**構文的**欠落ゼロ | `a11y-static-gate`（`scripts/a11y-static-check.py`・exit code） |
| **自動テスト**（CI） | 実際に描画された結果での役割・ラベル・タップサイズ・コントラスト・**最大文字サイズでの崩れ**・読み上げ順 | Flutter: `examples/a11y/flutter/a11y_smoke_test.dart`（組込 4 ガイドライン + role/記号ラベルの自作ガイドライン + AX5/200% の overflow 検査）/ Web: `eslint-plugin-jsx-a11y` + axe-core（`examples/a11y/web/eslint-a11y.config.md`） |
| **人手・実機**（リリース前） | 読み上げだけで主要導線が通るか・ラベルが**理解できる**か・拡大時に読めるか | VoiceOver / TalkBack で通し操作、最大文字サイズでスクリーンショット確認 |

**Flutter の重要な穴（実測・Flutter 3.44.2）**: 組込の `labeledTapTargetGuideline` は **role を見ず**、`Text('<')` も**非空ラベルとして通す**。つまり「ラベルはあるが役割が無い」「記号がラベル」の 2 つは組込ガイドラインでは緑になる。kit はこの 2 つを静的ゲート（`A11Y-FLUTTER-ROLE` / `A11Y-FLUTTER-SYMBOL-LABEL`）と自作ガイドライン（`ButtonRoleGuideline` / `MeaningfulLabelGuideline`）の両方で塞ぐ。

---

## 5. 自動化できないもの（ここを人手に残すと決める）

- 読み上げだけで主要導線が完了できるか（実機 VoiceOver / TalkBack）
- ラベルが理解できる語か（`戻る` が適切かは機械には判断できない・日本語の読み上げ品質も OS の TTS 依存）
- 画像・グラデーション上の文字のコントラスト
- 拡大時に「読める」か（overflow ゼロ ≠ 読みやすい）
- 視覚順と読み上げ順のずれが実際に混乱を生むか

---

## 6. 参照

- WCAG 2.2（W3C Recommendation・2024-12-12）: https://www.w3.org/TR/WCAG22/
- ネイティブアプリへの適用（WCAG2ICT）: https://www.w3.org/TR/wcag2ict-22/
- Flutter accessibility / testing: https://docs.flutter.dev/ui/accessibility ・ https://docs.flutter.dev/ui/accessibility/accessibility-testing
- Apple VoiceOver / Larger Text の評価基準（App Store の Accessibility Nutrition Labels）: https://developer.apple.com/help/app-store-connect/manage-app-accessibility/overview-of-accessibility-nutrition-labels
- WordPress Accessibility Ready 要件: https://wpaccessibility.org/docs/accessibility-ready/theme-guidelines/
- 導入手順・レガシー repo への段階適用・事業/法令の背景: [`docs/a11y-guide.md`](../../docs/a11y-guide.md)
