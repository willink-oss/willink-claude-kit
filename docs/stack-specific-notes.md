# スタック別注意点

kit は stack-agnostic に設計しているが、各スタックでの dev-tester / project-standards の埋め方には固有の知見がある。

---

## Next.js + TypeScript

### dev-tester が呼ぶコマンド
```bash
pnpm lint        # ESLint
pnpm typecheck   # tsc --noEmit
pnpm test        # Vitest / Jest
pnpm build       # Next.js build
```

### project-standards に書くべきこと
- App Router vs Pages Router の方針
- Server Components / Client Components の境界ルール
- データフェッチ戦略（fetch + cache / SWR / React Query）
- Tailwind / CSS Modules / shadcn-ui の使い分け

### よくある dev-reviewer 指摘
- Server Component で `useState` を使ってしまう
- `'use client'` の付け忘れ／過剰付け
- Image / Font の最適化漏れ

---

## Flutter

### dev-tester が呼ぶコマンド
```bash
flutter analyze       # 静的解析
flutter test          # ユニット
flutter build ios     # or appbundle / web
# integration_test は時間かかるので Phase 4 では選択的に
```

### project-standards に書くべきこと
- 状態管理（Riverpod / Bloc / Provider）の方針
- BaaS client の DI / scope 戦略
- DB 認可ポリシー（RLS 等）の管理場所と更新フロー
- ネイティブ固有: 権限文言・consent modal の場所

### よくある dev-reviewer 指摘
- DB 認可ポリシーが schema 追加に追従していない
- `BuildContext` を async gap 越しに使う（mounted チェック漏れ）
- ネイティブ SDK の権限を request せずに read 呼出
- 外部 AI / センシティブデータ系 SDK の consent modal 漏れ

### モバイルアプリ審査対応プロダクトの注意

第三者審査（App Store / Play Store）対応中のプロダクトは以下を kit 検証対象外とする:

**触ってはいけない領域**:
- `Info.plist` / `AndroidManifest.xml`
- Privacy disclosure（プライバシーポリシー本文）
- 審査提出予定ビルドの主要ファイル
- App Privacy Labels に影響する SDK 追加・削除

**安全な検証領域**:
- 内部ロジックの refactor
- ユニットテスト追加
- docs / コメント整備
- 開発 tool（lint / formatter）設定改善

---

## WordPress + PHP

> **支援レベル: 📝 documented, unverified**。本セクションは first-party guidance だが、
> kit を WordPress repo に対して end-to-end で検証した実績はない（best-effort）。
> 詳細は [`known-stack-coverage.md`](known-stack-coverage.md#wordpress-and-php-support-level) を参照。

### dev-tester が呼ぶコマンド
```bash
composer install
composer lint          # PHPCS
composer test          # PHPUnit (あれば)
# wp-env start でローカル動作確認
```

### project-standards に書くべきこと
- テーマ慣習（ACF / CPT / 命名）
- PHP version 制約（古い案件は 7.4、新規は 8.x）
- Atomic Design の category と命名規則
- NDA に関わる情報は kit に書かない（NDA repo の内部のみ）

### よくある dev-reviewer 指摘
- `wp_unslash()` / `sanitize_*()` / `esc_*()` の境界処理漏れ
- nonce 検証漏れ
- query 直書き（プリペアドステートメント未使用）
- カスタム JS が wp_enqueue_script でなく hard inline

---

## 共通: dev-tester の "early victory" 防止

どのスタックでも:
- `--bail` フラグを外して全テスト実行
- skipped/pending 件数を必ず確認
- build artifact の生成も確認（テストだけ pass で build 失敗を見逃さない）
