# 第三者コンポーネントと、公開版との関係

## 1. 第三者コード

**同梱していません。**

全 13 エンジン（約 6,900 行）は合同会社 i-Willink が作成したものです。
外部ライブラリへの依存はありません（Python3 標準ライブラリと POSIX shell のみ）。
`./verify.sh` の検査 5 が、ネットワーク呼び出し 0 件を毎回実測します。

## 2. 弊社が無料公開している版との重複

弊社は [willink-claude-kit](https://github.com/willink-oss/willink-claude-kit) を
**MIT ライセンスで無料公開**しています（skill 17 本）。

購入前に知っておくべき重複が 1 件あります。

| 同梱ファイル | 無料版での入手 | 差分 |
|---|---|---|
| `scripts/engines/goal-loop.sh` | [同 kit の `scripts/goal-loop.sh`](https://github.com/willink-oss/willink-claude-kit)（MIT・英語版） | 本同梱版は日本語 + 9 ケースの `--self-test` を追加 |

両者は同一の原型（弊社の社内実装）から派生しています。
**機能は等価です。** 無料版で足りる場合、この 1 本のために購入する必要はありません。

また、本ハーネスの 26 skill と kit の 17 skill に**重複はありません**が、
`judge-rubric-vote` `live-state-verify-guard` `maker-checker-relay`
`adversarial-refute-vote` `commit-convention-gate` `coverage-floor-lock`
`token-codegen-gate` `architecture-parity-gate` `self-heal-ci`
`fanout-verify-synth` `cogload-dashboard` `codex-imagegen` は
**同じ設計思想の同系譜で、MIT で無料公開されています**。

これらが必要な場合は、無料版を先に取得してください。
本ハーネスと併用できます（skill 名が衝突しないため、同じ `.claude/skills/` に共存します）。

## 3. 参考にした外部資料

- Claude 公式ドキュメント（Skills / Hooks / Agent SDK の仕様）
- Anthropic のブログ記事（ループ設計・サブエージェント運用のパターン）

いずれも仕様・概念の参照であり、コードの取り込みはありません。
