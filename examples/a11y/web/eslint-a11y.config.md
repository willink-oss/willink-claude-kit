# Web (Next.js / React) — a11y を既定にする配線

3 層に分ける。①lint（書いた瞬間）②axe(実行時 DOM)③reflow/拡大。どれも無料 OSS で、
どれも exit code で判定できる。

---

## 1. eslint-plugin-jsx-a11y を「明示的に」有効化する

`eslint-config-next`（`next/core-web-vitals`）は jsx-a11y を**同梱しているが有効化するのは
一部のルールだけ**。「next lint を使っている」は「a11y を lint している」ではない。
kit の静的ゲートはこの状態を `A11Y-PROJ-WEB-LINT-SUBSET`（warn）として区別して報告する。

```js
// eslint.config.mjs (flat config)
import next from "eslint-config-next/core-web-vitals";
import jsxA11y from "eslint-plugin-jsx-a11y";

export default [
  ...next,
  {
    // ⚠️ `jsxA11y.flatConfigs.recommended` を spread してはいけない。
    // eslint-config-next が既に jsx-a11y プラグインを登録しているため、ESLint 9 は
    // `Cannot redefine plugin "jsx-a11y"` で **起動しなくなる**（= lint が 1 行も走らない）。
    // プラグイン定義ではなく **rules だけ** を上乗せする。
    rules: {
      ...jsxA11y.flatConfigs.recommended.rules,
      // 既定では warn のものを error に上げる（warn は放置され exit 0 のまま）
      "jsx-a11y/alt-text": "error",
      "jsx-a11y/anchor-is-valid": "error",
      "jsx-a11y/click-events-have-key-events": "error",
      "jsx-a11y/no-static-element-interactions": "error",
      "jsx-a11y/interactive-supports-focus": "error",
      "jsx-a11y/label-has-associated-control": "error",
      "jsx-a11y/aria-props": "error",
      "jsx-a11y/role-has-required-aria-props": "error",
    },
  },
];
```

```jsonc
// package.json — warn で exit 0 になるのを防ぐ
{
  "scripts": {
    "lint": "eslint . --max-warnings 0"
  }
}
```

> **Next.js 16 以降は `next build` が lint を実行しない**。CI に独立した `eslint` ステップが
> 必要（無いと「lint が緑」ではなく「lint が走っていない」になる）。

### lint 設定そのものの生存確認（fixture 方式）

設定ミスや ignore パターンで**全ファイルが未検査になっても lint は緑になる**。
kit の hook テストと同じ block/pass 両ケースで設定自体を検査する。

```bash
# tests/a11y-fixtures/bad.tsx  → exit 1 になること
# tests/a11y-fixtures/good.tsx → exit 0 になること
npx eslint tests/a11y-fixtures/bad.tsx  && { echo "GATE DEAD: bad fixture passed"; exit 1; }
npx eslint tests/a11y-fixtures/good.tsx || { echo "GATE NOISY: good fixture failed"; exit 1; }
```

```tsx
// tests/a11y-fixtures/bad.tsx — 既知違反（検出されなければ設定が死んでいる）
export const Bad = () => (
  <div>
    <div onClick={() => {}}>保存</div>
    <img src="/x.png" />
    <a>リンク</a>
  </div>
);
```

---

## 2. axe-core を実行時 DOM に当てる（violations だけでなく incomplete も出す）

```ts
// e2e/a11y.spec.ts — @axe-core/playwright（無料 OSS）
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

for (const colorScheme of ["light", "dark"] as const) {
  test(`no a11y violations (${colorScheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme });
    await page.goto("/");
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();

    // incomplete は「判定できなかった」＝人が見るキュー。捨てると静かなゼロになる。
    console.log(`axe: ${results.violations.length} violations / ${results.incomplete.length} incomplete`);
    expect(results.violations).toEqual([]);
  });
}
```

- **ダークモードは別テーマとして回す**。コントラストは片側だけ落ちるのが典型
- `new AxeBuilder({ page })` は `browser.newPage()` のページで例外になる。`browser.newContext()`
  から作ったページを使う
- **`target-size`（SC 2.5.8）は axe の既定で無効**。明示的に有効化する:
  `.options({ rules: { "target-size": { enabled: true } } })`
- axe が自動検出できるのは WCAG 違反の一部（Deque 自身の調査で issue 件数ベース約 57%:
  https://www.deque.com/blog/automated-testing-study-identifies-57-percent-of-digital-accessibility-issues/ ）
- **Lighthouse の accessibility スコアを a11y ゲートにしない**。Lighthouse は accessibility を
  「自動監査 + manual 監査」で構成し、**manual 側はスコアに一切影響しない**。その manual 項目が
  `custom-controls-labels` / `custom-controls-roles` / `focusable-controls` / `logical-tab-order`
  ＝まさに「自作ボタンに role が無い」系なので、**スコア 100 と「読み上げで操作不能」は両立する**
- `pa11y` も使わない（既定エンジン HTML_CodeSniffer が WCAG 2.2 未対応で 2.4.11 / 2.5.8 が構造的に未検査）
- **アイコンのみボタンと記号ラベル（`×` `<`）は lint も axe も検出しない**（axe は「discernible text あり」
  として合格させる）。ここは kit の静的ゲート（`A11Y-WEB-CONTROL-NAME` / `A11Y-WEB-SYMBOL-LABEL`）が担う

---

## 3. 文字 200% と 320px reflow（SC 1.4.4 / 1.4.10）

```ts
test("reflow at 320px and text at 200%", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 256 });
  await page.goto("/");
  const overflowsAt320 = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  expect(overflowsAt320, "320px で横スクロールが発生している").toBe(false);

  await page.addStyleTag({ content: "html { font-size: 200% !important; }" });
  const overflowsAt200 = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  expect(overflowsAt200, "文字 200% で横スクロールが発生している").toBe(false);
});
```

意図的な横スクロール領域（データテーブル・地図・カルーセル）は SC 1.4.10 の正規の例外。
`data-a11y-allow-overflow` のような opt-out 属性で明示し、**opt-out の件数をログに出す**
（黙って除外すると「対応済み」に見えてしまう）。

---

## 4. CI

```yaml
- run: pnpm lint                    # jsx-a11y（error 昇格 + --max-warnings 0）
- run: pnpm test:a11y-fixtures      # lint 設定自体の生存確認
- run: pnpm exec playwright test e2e/a11y.spec.ts   # axe + reflow
- run: python3 scripts/a11y-static-check.py --root . --baseline .a11y-baseline.json
```

最後の 1 行が kit の静的ゲート。lint より前の層（`.php` テンプレートや Flutter も同じ 1 コマンドで
見る）を担い、`eslint` が見ない拡張子を埋める。
