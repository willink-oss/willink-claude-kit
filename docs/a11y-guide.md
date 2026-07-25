# Accessibility — 既定で設計し、決定論的に守る

「アクセシビリティに配慮する」は指示としては確率的で、守られたか確認できない。この kit は
a11y を 3 層で扱う: **設計時の契約**（[`a11y-standards`](../skills/a11y-standards/)）→
**毎コミットの決定論ゲート**（[`a11y-static-gate`](../skills/a11y-static-gate/)）→
**実行時テストと実機**（`examples/a11y/`）。

- 何を守るか → [`skills/a11y-standards/SKILL.md`](../skills/a11y-standards/SKILL.md)
- どう機械判定するか → [`skills/a11y-static-gate/SKILL.md`](../skills/a11y-static-gate/SKILL.md)
- CI 配線と ratchet → [`examples/ci/a11y-gate-pattern.md`](../examples/ci/a11y-gate-pattern.md)

本書は**導入手順**と、そこに至った**実測の根拠**を扱う。

---

## 1. なぜ後付けが効かないか（この標準の出発点）

社内の Flutter 製チャットアプリで a11y 監査を行った結果:

- 操作要素のほぼ全てが「`GestureDetector` + `Text`/`Container` の自作ボタン」で、**button role を持たない**
- 戻る導線は記号 `<` を表示しており、読み上げは「**小なり**」
- アイコン導線（日記/設定/追加）は**ラベルが無く無名の要素**として読まれる
- 明示的な `Semantics` はチャット気泡系の数ノードのみ
- 端末の文字サイズを最大にすると、固定寸法のメッセージカードが **RenderFlex overflow**

→ 結論は「音声のみでの通し操作（ログイン → ペア設定 → 送受信）が成立しない」。

重要なのは**個々のバグではなく分布**で、共通ボタン Widget が name/role を持たないために
**全画面が同時に壊れていた**。逆に言えば、共通 Widget 1 ファイルに name/role/state を入れると
主要画面が一斉に治る。だから a11y は「設計時に 1 回決める」ものとして扱う。

このリポジトリの静的ゲートは、上記の監査結果を**独立に再現**する（共通ボタン Widget の該当行、
記号ラベルの戻るボタン 9 箇所、無名アイコン導線を検出）。しかも監査所見より精密で、
「コピー/共有ボタンはラベル欠落」ではなく「**ラベルはあるが role/state が欠落**」と分類した。

---

## 2. 新規プロジェクトへの導入（違反 0 が前提）

```bash
# 1. ゲートを回す（stdlib python3 のみ・依存なし）
python3 scripts/a11y-static-check.py --root .

# 2. 実行時テストを置く
cp examples/a11y/flutter/a11y_smoke_test.dart <project>/test/a11y_smoke_test.dart   # Flutter
#   examples/a11y/web/eslint-a11y.config.md の設定を eslint / axe に反映            # Web

# 3. project-standards に「このプロジェクトの具体値」を書く
#    （共通ボタン Widget 名・対応 AT・クランプ値・除外領域とその理由）
```

CI は [`examples/ci/a11y-gate-pattern.md`](../examples/ci/a11y-gate-pattern.md) の形。
`|| true` と `continue-on-error` を付けない（付けた瞬間にゲートは装飾になる）。

## 3. 既存（レガシー）プロジェクトへの導入

違反が既に 50 件あるリポジトリに「違反 0」を要求すると、翌日ゲートが外される。
**現状を凍結して、増分だけを止める**。

```bash
# 現状を baseline に固定（PR に件数を書いて債務を記録する）
python3 scripts/a11y-static-check.py --root . \
    --baseline .a11y-baseline.json --update-baseline
git add .a11y-baseline.json
```

- 以後 CI は `--baseline .a11y-baseline.json` 付きで回し、**新規違反のみ落ちる**
- baseline は**減る方向にのみ**更新できる（増やすには `--allow-baseline-growth` が必要で、
  痕跡が diff と PR に残る）
- **baseline は必ずスキャナ自身の出力から作る**。監査レポートの件数を手で転記しない
  （grep 行数ベースの件数は二重計上を含み、実際の distinct な違反数と一致しない）
- 修正の順序: ①共通 Widget / 共通コンポーネント（1 箇所で全画面に効く）→ ②主要導線
  （ログイン・課金・問い合わせ）→ ③残り

月次で見る数値は baseline の `counts.error` 1 つ。単調減少が期待値。

---

## 4. 「対応済み」と言える範囲（表記の線引き）

自動検出でカバーできるのは違反の一部（Deque の調査で issue 件数ベース約 57%）。
したがって:

| 書ける | 書けない（人手監査 + 社長承認が必要 = Level 3） |
|---|---|
| 「機械判定可能な a11y 違反ゼロ（走査 N ファイル）」 | 「WCAG 2.2 AA 準拠」 |
| 「VoiceOver で主要導線を通しで確認済み（日付・端末・OS 版）」 | 「アクセシブル」「フルアクセシビリティ対応」 |

受託契約・LP・ストア申告に準拠表記を載せるのは法的リスクを伴う。ゲートの緑は
**準拠の証明ではない**。

### 特に信用してはいけない指標

- **Lighthouse の accessibility スコア**: 自動監査と manual 監査で構成され、**manual 側はスコアに
  影響しない**。その manual 項目が `custom-controls-labels` / `custom-controls-roles` /
  `focusable-controls` / `logical-tab-order` ＝まさに「自作ボタンに role が無い」系なので、
  **スコア 100 と「読み上げで操作不能」は両立する**
- **Flutter 組込の `labeledTapTargetGuideline`**（実測・Flutter 3.44.2）: label **または tooltip** が
  非空なら通る。**role を見ない**し、`Text('<')` も**非空ラベルとして通す**。つまり §1 の
  2 大欠陥はこのガイドラインでは緑になる
- **axe / eslint-plugin-jsx-a11y**: アイコンのみボタンと記号ラベル（`×`）を検出しない
  （axe は「discernible text あり」として合格させる）
- **空の実行結果**: 走査 0 件は「違反 0」ではない。ゲートはこれを exit 3（UNKNOWN）で返す

---

## 5. プラットフォーム側の要件（事業インパクト）

### Apple — Accessibility Nutrition Labels

App Store の製品ページに申告する 9 項目（VoiceOver / Voice Control / Larger Text /
Dark Interface / Differentiate Without Color Alone / Sufficient Contrast / Reduced Motion /
Captions / Audio Descriptions）。**現状は任意だが、Apple は将来必須化を明言**している
（「over time, you'll be required to share accessibility support details to submit new apps and
app updates」）。判定単位は「common tasks」（primary functionality / first launch / login /
purchase / settings）で、**その全てで機能が使えないと申告できない**。

- 申告は App Store Connect の App Accessibility でデバイス単位（App Store Connect API にも
  `accessibility-declarations` エンドポイントがある）
- **Larger Text の条件は「200% または システム最大まで拡大できる」**。したがって
  文字拡大を 2.0 未満にクランプすると申告できない（ゲートの `A11Y-FLUTTER-SCALE-CLAMP` がこれを見る）
- VoiceOver の評価基準は**ラベルとトレイト（role/state）を明確に分離**することを要求する
  （「Labels should NOT include control types like 'checkbox' or states like 'checked'」）
- App Store Review Guidelines 本体に a11y の要求条項は無い。ただし **editorial featuring の
  公式観点 7 つのうち 1 つが Accessibility**（"A great experience for a broad range of users with
  well integrated features."）。Featuring Nominations は最低 3 週間のリードタイム

出典: https://developer.apple.com/help/app-store-connect/manage-app-accessibility/overview-of-accessibility-nutrition-labels
・ https://developer.apple.com/app-store/getting-featured/

### WordPress — Accessibility Ready（受託テーマ）

`accessibility-ready` タグを謳うテーマの要件は **2026-05-06 改訂で全 18 項目が必須**になり
（旧「推奨」区分は廃止）、テーマルートに **`accessibility.txt`** を置くことも必須。
対応期限は **2026-09-30** に延長され、**10 月 1 日以降、再レビュー未申請のテーマはディレクトリから
de-list** される。

- ただし公式が「`Accessibility Ready` は WCAG AA 準拠を意味しない」と明記している。
  テーマレビューチームの**最低基準**であって、準拠表記の根拠にはならない
- 数値要件: 本文 4.5:1 / 大きい文字 3:1 / UI 部品 3:1、フォーカスは**最小 2px の outline**、
  reflow は 1280px 幅で 200%→400% ズームで横スクロールが出ないこと
- フォームは**可視ラベル + `for`/`id` の明示的関連付け**が必須（placeholder は代替不可）
- 本文中リンクは**下線必須**（色だけの区別は不可）。`read more` のような文脈依存テキストは不可
- PHP 側に a11y の静的解析経路は実質存在しない（`WPThemeReview` は 2021 年に更新停止）。
  この kit の静的ゲート（`A11Y-MARKUP-*`）＋ rendered DOM への axe の 2 層で埋める

出典: https://wpaccessibility.org/docs/accessibility-ready/theme-guidelines/
・ https://make.wordpress.org/accessibility/2026/07/23/accessibility-ready-theme-reviews-extending/

---

## 6. 静的ゲートの限界（正直な記載）

- **ラッパー越しは見えない**: 共通ボタン Widget 経由の呼び出し側は検査対象にならない。
  ゲートは**ラッパー定義そのもの**を検出し、加えて「分類できない tappable コンストラクタ」を
  `A11Y-FLUTTER-UNKNOWN-TAP-CTOR`（warn・ファイル単位で集約）として**必ず可視化**する
  — ハードコードした既知リストのドリフトを黙って通さないための設計
- **動的に組まれるラベル**は「名前がある」として扱う（過検出を避けるため）。実際に意味のある語かは
  人手判断
- **CSS のカスケード**は静的に解けない（`outline: none` の復活検出などは advisory 止まり）
- **`--release` では overflow の assert が消える**ため、Flutter の拡大テストは debug でのみ有効
- ゲートは検出のみで、**修正も CI への自己配線もしない**

---

## 7. 関連

- [`skills/a11y-standards/`](../skills/a11y-standards/) — 設計時の契約（agents が preload する）
- [`skills/a11y-static-gate/`](../skills/a11y-static-gate/) — 決定論ゲートの rule 一覧と exit code
- [`examples/a11y/flutter/a11y_smoke_test.dart`](../examples/a11y/flutter/a11y_smoke_test.dart) — 組込 4 ガイドライン + role/記号ラベルの自作ガイドライン + 最大文字サイズの overflow 検査
- [`examples/a11y/web/eslint-a11y.config.md`](../examples/a11y/web/eslint-a11y.config.md) — jsx-a11y / axe / reflow
- [`docs/harness-profile.md`](harness-profile.md) — 決定論ゲートのラダー（H1-H4）における a11y の位置
