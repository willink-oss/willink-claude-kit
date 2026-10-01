# 開発標準

> **本書はハーネス正本リポの文書である。** 2026-09-09 に運営リポから移送した。
> 運営リポ側の同名ファイルは本書へのポインタであり、**編集は本書に対して行う**。

制定日: 2026-03-18（運営リポ）／本リポへの移送: 2026-09-09
適用: 本標準を取り込むすべてのリポジトリ（Core として配る）

---

## 0. 本書の位置づけ

**プロダクト横断の開発基準。** 技術スタックの既定・品質・API・DB・セキュリティ・リリース・見積りを扱う。
**どのリポジトリに何を適用しているかの台帳は本書に持たない**（運営リポ側にある）。本書は「どう作るか」だけを持つ。

### 本書が持たないもの

| 種類 | どこにあるか |
|---|---|
| 決定（ADR） | 運営リポ。**本リポは決定を持たない** |
| 適用台帳（リポごとの Tier / 受託案件 / 必須チェック / 監視対象） | 運営リポ |
| プロジェクト別の技術マッピング（どの製品が何を使っているか） | 運営リポ |
| ブランチ運用・リリースフローの正本 | 運営リポ。**§2-2 命名 / §5 タグ / §10 並行開発の汎用部は M1 で本リポへ移す** |
| 承認レベル・部門マニュアル・Drive 運用 | 運営リポ |

### プロジェクト固有のオーバーライド

各プロジェクトの `CLAUDE.md` に `## Standards Overrides` を設け、本標準からの逸脱を**理由付きで**明記する
（様式は §15）。**逸脱そのものは問題ではない。逸脱が記録されていないことが問題である。**

---

## 1. 技術スタック標準

### デフォルト選定

| 領域 | デフォルト | 代替（要理由・Overrides 記載） |
|---|---|---|
| **Web Frontend** | Next.js + React + Tailwind CSS | — |
| **Mobile** | Flutter (Dart) + Riverpod | React Native |
| **Backend** | **Cloud Functions for Firebase**（TypeScript / Gen2） | Cloud Run / NestJS / FastAPI（AI 系） |
| **Database** | **Firestore** | Cloud SQL / PostgreSQL (Supabase) / DynamoDB |
| **認証** | **Firebase Auth** | Supabase Auth / Identity Platform |
| **ストレージ** | **Firebase Storage / Cloud Storage** | — |
| **ホスティング** | **Firebase Hosting**（動的が要るなら **Cloud Run**） | — |
| **IaC** | **Firebase CLI + `firebase.json` / rules / indexes**（GCP の恒久資源は Terraform） | AWS CDK（**Route53 / SES の配線に限る**） |
| **CI/CD** | GitHub Actions | Xcode Cloud (iOS) |
| **モノレポ** | Turborepo + pnpm | — |
| **AI/LLM** | Claude API (primary) | OpenAI, Gemini |
| **UIコンポーネント** | shadcn/ui | — |
| **CSS** | Tailwind CSS | CSS Modules |

### 1-1. AWS の利用範囲

**新規に AWS を使ってよいのは 2 つだけ**:

1. **Route53**（DNS）
2. **メール送信（SES）** — および SES を動かすのに必要な最小限の配線（IAM / Lambda / CDK stack）

これ以外の AWS サービスを**新規に**採用するのは **Level 3（事前承認）**。DynamoDB / Cognito / Amplify / App Runner / アプリのデータ面としての S3 が該当する。

> **稼働中システムの移行は本標準の対象外。** 社内の決定記録 は「新規選定の既定」を変えたものであり、既に稼働している実装（旧既定で作った DB・認証・ホスティング）はそれぞれ別判断。Overrides に記録して残す。

> **この決定は宣言ではなく live 実測から出した。** 既定を変える時は「どちらが良いか」を論じる前に、
> **各サービスの現用リソース数を実際に数える**。当社の 2026-09-08 実測では、DNS とメールは現役だった一方、
> それ以外の AWS マネージドサービスは**アプリからの利用が 0 件**だった（既定と実態が逆転していた）。
> 数える前に反転させない。

### 1-2. Firebase を既定にすると付いてくるもの（採用時に毎回確認する）

- **課金は従量。予算アラートは支出を止めない**（Google 公式）。歯止めはクエリ設計・App Check・契約条項側に置く
- **Firestore の無料枠はプロジェクト単位**（1 プロジェクト 1 無料 DB）。テナントを分けたいなら**プロジェクトを分ける**。名前付き DB は Auth / Functions / 請求を分けない
- **Firestore のロケーションは不可逆** — プロジェクト作成時の選択は **Level 3**
- **Security Rules をデータモデルより先に書かない**（順序: データモデル → Rules）。先に書くと全部書き直しになる

### バージョンポリシー

- メジャー版はN-1まで許容（例: Next.js 16が最新なら15もOK）
- LTS版を優先採用
- 新技術の導入は **Level 3（事前承認）** — 承認レベル（運営リポ） 参照

---

## 2. プロジェクト初期化チェックリスト

新規プロジェクト作成時の必須ファイル:

```
project-root/
├── CLAUDE.md                        # PJ固有のルール・コンテキスト
├── .claude/commands/                 # dev.md, test.md, deploy.md, review.md
├── .cursorrules                      # Cursor IDE用ルール
├── .github/
│   ├── copilot-instructions.md       # Copilot用指示
│   └── workflows/ci.yml             # CI/CDパイプライン
├── README.md                        # セットアップ手順・コマンド一覧
├── .gitignore
├── .editorconfig                    # インデント・改行統一
├── .env.example                     # 環境変数テンプレート（値は空）
├── tsconfig.json                    # strict: true 必須
├── eslint.config.mjs                # ESLint Flat Config
├── prettier.config.mjs              # Prettier設定
└── LICENSE                          # OSS: MIT推奨 / 受託: UNLICENSED
```

### package.json scripts 標準

```json
{
  "dev": "開発サーバー起動",
  "build": "本番ビルド",
  "start": "本番サーバー起動",
  "test": "ユニットテスト実行",
  "test:e2e": "E2Eテスト実行",
  "lint": "ESLint実行",
  "lint:fix": "ESLint自動修正",
  "format": "Prettier実行",
  "typecheck": "tsc --noEmit"
}
```

### 初回コミット

```
chore: initial project setup
```

→ AI-native設定の詳細は Claude Code 統合ガイド（運営リポ） 参照

### リポジトリクローン後のセットアップ

共有 git hooks を有効化するため、クローン直後に一度だけ実行する（**運用リポ・プロダクトリポとも**）:

```sh
git config core.hooksPath .githooks
```

これにより `.githooks/pre-commit`（CLAUDE.md 行数ガード + `.claude/hooks/pre-commit-quality.sh` 呼び出し）が有効になり、
Claude Code 経由か手動 `git commit` かを問わず品質チェックが走る。**設定しないと hooks は 1 本も動かない** —
「入れた」と「効いている」は別なので、導入後に故意に違反させて block を 1 回実測する。

---

## 3. コード品質基準

→ 詳細: 開発部門マニュアル「コード品質基準」+ コードレビューチェックリスト（いずれも運営リポ）

### 補足ガイドライン

- **関数サイズ**: 50行以内を目安（超える場合は分割を検討）
- **ファイルサイズ**: 300行以内を目安
- **テストカバレッジ**: 新規コード80%以上、プロジェクト全体60%以上
- **コミット形式**: Conventional Commits。許容 prefix の正本は `.githooks/commit-msg`（`feat` / `fix` / `docs` / `refactor` / `test` / `chore` / `harness` / `pm` / `ops` / `learn` / `config` / `design` / `legal` / `style` / `perf` / `ci` / `build` / `revert` / `pr` の19種）
- **ブランチ戦略**: trunk-based。長命ブランチは `main` のみで、短命ブランチ `<type>/<slug>`（`type` は commit prefix と同一語彙・単数形）を切って数時間〜数日で main に戻して消す。→ 正本: ブランチ管理・リリースフロー標準 §2（運営リポ）
- **日本語タイポグラフィ（和文 UI/公開物）**: 見出し・タイトルは**語中で折り返さない**こと。CJK は既定でどの文字間でも改行するため、見出し（h1..h4）に `word-break: auto-phrase`（+ `line-break: strict`）をグローバル base で効かせる。コンポーネント個別でなくグローバル規則で担保し、**回帰テストで機械ガード**する（例: i-willink.com `apps/web/src/__tests__/typography-jp-wrap.test.ts`）。完全クロスブラウザ化が要る場合は BudouX を検討

---

## 4. 環境管理

### 環境定義

| 環境 | 用途 | データ | アクセス |
|---|---|---|---|
| **local** | 開発・デバッグ | モック/ローカルDB | 開発者のみ |
| **dev** | 統合テスト | テストデータ | 開発チーム |
| **staging** | リリース前検証 | 本番に近いデータ | 社内全員 |
| **production** | 本番サービス | 本番データ | エンドユーザー |

※ 1人+AI体制では local + production の2環境で十分な場合が多い。必要に応じてdev/stagingを追加。

### 環境変数管理

- `.env.example` をGit管理（値は空、キー名のみ）
- `.env.local` はGit管理外（`.gitignore` に記載）
- 本番: **GCP Secret Manager**。Route53 / SES の配線に使う認証情報だけは AWS Secrets Manager / Parameter Store でよい
- 環境変数にシークレットを直接書かない（CI/CDのシークレット機能を使用）
- **runtime env の変更は Level 3**（self-lockout・`standards/approval-levels.md`）

---

## 5. 依存関係管理

- **パッケージマネージャー**: pnpm（Web）、pub（Flutter）
- **lockfile**: 必ずコミット（`pnpm-lock.yaml`, `pubspec.lock`）
- **更新ポリシー**:
  - セキュリティパッチ → 即時適用
  - マイナー/パッチ → 月次確認
  - メジャー → 四半期レビューで判断
- **監査**: `pnpm audit` を月次実行、Critical/Highは即時対応
- **ライセンス許可**: MIT, Apache-2.0, BSD, ISC
  - GPL系 → **Level 3（事前承認）**、商用プロダクトでの使用は原則禁止

---

## 6. エラー処理・ログ標準

### エラー処理の原則

- システム境界（APIリクエスト受付、外部サービス呼び出し）でのみ `try-catch`
- 内部ロジックは型安全性で防止（不要な防御的コーディングを避ける）
- ユーザー向けメッセージと開発者向けログを分離

### ログレベル

| レベル | 用途 | 例 |
|---|---|---|
| **ERROR** | 即時対応が必要 | DB接続失敗、決済エラー |
| **WARN** | 注意が必要だが動作は継続 | レート制限接近、非推奨API使用 |
| **INFO** | 正常な業務イベント | ユーザー登録、デプロイ完了 |
| **DEBUG** | 開発時のデバッグ情報 | リクエスト詳細、SQL |

### ルール

- 構造化ログ（JSON形式）: `{ timestamp, level, message, context, requestId }`
- 出力先: **Cloud Logging**（Firebase / Cloud Run / Cloud Functions）。Route53 / SES 側は CloudWatch Logs
- **PII（個人情報）、トークン、パスワードのログ出力禁止**

---

## 7. API設計標準

### REST API規則

- URL: 複数形名詞、ケバブケース（`/api/v1/user-profiles`）
- HTTPメソッド: GET（取得）、POST（作成）、PUT（全更新）、PATCH（部分更新）、DELETE（削除）
- バージョニング: URLパス（`/v1/`）、重大な破壊的変更時のみインクリメント

### レスポンス形式

```json
// 成功
{ "data": { ... }, "meta": { "page": 1, "total": 100 } }

// エラー
{ "error": { "code": "VALIDATION_ERROR", "message": "...", "details": [...] } }
```

### ページネーション

- cursor-based を標準（DynamoDB互換、オフセット問題を回避）
- クエリパラメータ: `?cursor=xxx&limit=20`

### 認証

- Bearer JWT（Cognito/Firebase Auth発行）
- API Gatewayでレート制限を設定

---

## 8. データベース設計原則

- **アクセスパターン起点**: Firestore も DynamoDB と同じくクエリパターンからコレクション設計を逆算する（正規化から入らない）
- **命名規則**: コレクション名 / テーブル名 PascalCase、フィールド名 camelCase
- **必須フィールド**: `id`, `createdAt`, `updatedAt`
- **ソフトデリート**: `deletedAt` フィールド使用（物理削除は原則禁止）
- **マイグレーション**: Firestore → **rules / indexes を repo で管理し `firebase deploy` で適用**（`firestore.rules` / `firestore.indexes.json` を必ずコミット） / RDB → Prisma Migrate / DynamoDB → CDK
- **client write の箇所数を設計書に明記する**: client から直接書ける場所を最小化し、残りは Cloud Functions（callable）へ寄せる
- **Security Rules は Emulator で deny 主体のテストを書く**（「書けること」ではなく「**書けないこと**」を検査する）
- **バックアップ**: 本番は **PITR（Point-in-Time Recovery）を有効化**（Firestore / RDS とも）

---

## 9. パフォーマンス基準

| 指標 | 目標値 |
|---|---|
| LCP (Largest Contentful Paint) | < 2.5秒 |
| FID (First Input Delay) | < 100ms |
| CLS (Cumulative Layout Shift) | < 0.1 |
| 初期JSバンドル (gzip) | < 200KB |
| API応答時間 P95 | < 500ms（Lambda cold start除く） |
| Lighthouse Performance | > 80 |
| Lighthouse Accessibility | > 90 |
| モバイルアプリ起動 | < 3秒 |

リリース前にLighthouseチェックを実施。WordPress案件はPageSpeed Insights 90+を目標。

---

## 10. アクセシビリティ標準

- **WCAG 2.1 Level AA** 準拠（最低限）
- セマンティックHTML（`<header>`, `<main>`, `<nav>`, `<article>` 等）
- キーボードナビゲーション対応
- 色コントラスト比: テキスト 4.5:1以上、大テキスト 3:1以上
- 画像に `alt` 属性必須、装飾画像は `alt=""`
- フォームに `label` 要素必須
- WordPress案件: アクセシビリティチェッカーを活用

---

## 11. ドキュメント標準

### プロジェクト必須ドキュメント

| ドキュメント | 条件 |
|---|---|
| README.md | 全プロジェクト |
| CLAUDE.md | 全プロジェクト |
| API仕様書 | REST APIがある場合 |
| アーキテクチャ設計書 | 中規模以上 |
| ADR | 重要な技術判断時 |

### 保存場所の境界

| 種類 | 保存先 | 理由 |
|---|---|---|
| ソースコード・技術ドキュメント | **Git** | バージョン管理・レビュー連動 |
| 設計書・ADR・戦略文書 | **Git** | 変更履歴・AI エージェントが参照 |
| 社内会議議事録 | **Git** | AI エージェントが参照 |
| 契約書・NDA | **Google Drive** | 機密性・署名付き |
| 請求書・領収書・経理書類 | **Google Drive** | 税務・バイナリ |
| デザインファイル（Figma/画像） | **Google Drive** | バイナリ、Git不向き |
| クライアント納品物 | **Google Drive** | 共有制御 |
| ストア申請素材 | **Google Drive** | 画像ファイル |
| 社外会議議事録 | **Google Drive** | 機密性・共有制御 |

→ Google Driveの管理ルール詳細は ドキュメント保管ガイドライン（運営リポ） 参照

---

## 12. セキュリティ標準

→ 詳細: コードレビューチェックリスト セキュリティセクション（運営リポ）

### 必須ルール

- **認証**: **Firebase Auth**（既定）/ Supabase Auth を使用（**自前実装禁止**）
- **通信**: HTTPS / WSS 必須（HTTP許可なし）
- **データ暗号化**: at-rest（DB暗号化）+ in-transit（TLS）
- **シークレット管理**: `.env` はGit管理外、本番は **GCP Secret Manager**
- **App Check**: Firebase を使うプロダクトは**初日から enforce**（後から入れると既存クライアントが落ちる）
- **脆弱性スキャン**: `pnpm audit` + Dependabot 有効化
- **OWASP Top 10**: リリース前に確認（code-review-checklist準拠）
- **secrets/credentials のハードコード禁止**

### 12-1. 開発ツール設定に認証情報を直書きしない（2026-08-13 制定）

上の「ハードコード禁止」はアプリケーションコードを想定しており、**開発ツールのローカル設定ファイルが射程から漏れていた**。実際に 2 件の平文トークンが実測で見つかったため、対象を明示する。

**禁止**: MCP サーバ定義および AI 開発ツールの設定ファイルに、トークン・PAT・API キーを直接書くこと。対象ファイルの例:

| ツール | ファイル |
|---|---|
| Codex | `~/.codex/config.toml`（`[mcp_servers.*.http_headers]` / `env`） |
| Claude Code | `~/.claude.json`・`.mcp.json`・`settings.json` の `env` |
| Antigravity / Gemini | `~/.gemini/config/mcp_config.json`（`env` / `args`） |

**必須**: 値は間接参照にする。優先順位は次のとおり。

1. **ツール固有の env var 参照機構** — Codex は `bearer_token_env_var`、Claude Code の plugin MCP は `${VAR}` 展開
2. **シェルの環境変数** — `export GITHUB_PERSONAL_ACCESS_TOKEN="$(gh auth token)"` のように、既存の認証済み CLI から都度導出する（値がファイルに残らない） <!-- pragma: allowlist secret -->
3. **OS のキーチェーン / Secrets Manager 経由**のラッパースクリプト

**設定から消すだけでは不十分**。AI ツールは会話ログを保存するため、`~/.codex/sessions/*.jsonl` や `~/.claude/history.jsonl` に値が残る。**必ず発行元で失効させる**こと。

**根拠となった実測事例（2026-08-12〜13）**:

| 事案 | 実測結果 |
|---|---|
| Codex `config.toml` の fine-grained PAT | **有効だった**（`GET /user` → 200）。個人 private repo の contents まで読める状態。`claude-code-pat` を Delete して 401 を確認 |
| Antigravity `mcp_config.json` の GitHub classic PAT | 失効済み（401）。実害なし |
| Antigravity `mcp_config.json` の Supabase PAT | **有効だった**（Management API → 200・プロジェクト 3 件）。Supabase は管理 API のため影響範囲が大きい |

3 件中 2 件が有効だった。「古い設定だから死んでいるはず」という推定は実測で否定されている — **必ず疎通を確認して判断する**。

**検出**: 現時点で自動ゲートは無い。定期的に次を実行して手で確認する（トークン形式は各サービスの prefix に依存するため網羅ではない）。

```bash
grep -rlE '(gh[pous]_|github_pat_|sbp_|sk-[A-Za-z0-9]{20,})' \
  ~/.codex/config.toml ~/.claude.json ~/.gemini/config/ 2>/dev/null
```

決定論ゲート化（`scripts/` に落として CI か pre-commit で回す）は未実施。ローカル設定は Git 管理外のため CI では検知できず、実行場所の設計が別途必要。

---

## 13. インシデント対応

### 重要度

| レベル | 定義 | 対応時間 |
|---|---|---|
| **P1** | サービス停止・データ漏洩 | 即時（発見次第） |
| **P2** | 主要機能の障害 | 当日中 |
| **P3** | 軽微な不具合・UI崩れ | 次営業日 |

### 対応フロー

1. **検知** → **Cloud Monitoring アラート** / CloudWatch Alarm（Route53・SES）/ ユーザー報告 / エラーログ
2. **影響範囲特定** → どのサービス・ユーザーに影響があるか
3. **緊急修正** → `fix/<slug>` ブランチを切り、通常の短命ブランチとして PR で main へ入れる（trunk-based では backmerge 先が存在しない）。→ ブランチ管理・リリースフロー標準 §2-1 / §3（運営リポ）
4. **根本原因分析** → なぜ発生したか
5. **再発防止** → テスト追加・監視強化

### ロールバック手順

- **Firebase Hosting**: コンソール または `firebase hosting:rollback` で直前バージョンへ
- **Cloud Run**: 直前リビジョンへ 100% トラフィックを戻す（`gcloud run services update-traffic --to-revisions`）
- **Cloud Functions**: 直前のソースで再デプロイ（無停止のロールバック機構は無い）
- **CDK**（Route53 / SES）: `cdk deploy --rollback`
- **コード**: `git revert` で修正コミットを打ち消し → 再デプロイ
- ⚠️ **Firestore のデータは revert で戻らない。** rules / indexes の巻き戻しと**データ**の巻き戻し（PITR）は別手順

### 1人+AI体制の現実的対応

- P1: メール通知（Cloud Monitoring / CloudWatch Alarm → SES）→ 即時対応
- P2/P3: 翌営業日対応で可（社長は副業のため）
- Postmortem: 社内の postmortem 置き場 に記録

---

## 14. リリース管理

→ **ブランチ運用・main への入り方・バージョニング/タグの正本は **ブランチ管理・リリースフロー標準**（運営リポ・§2-2 / §5 / §10 の汎用部は M1 で本リポへ移す）**。本節はその要約に留める。

### リリースフロー（Web / サーバー）

```
<type>/<slug> ブランチ → PR → required check（機械検証）→ 対象 PR は Verifier 検証コメント → gh pr merge --auto で main → 自動デプロイ → live 検証
```

- 統合ブランチは持たない。main が唯一の長命ブランチで常にデプロイ可能。
- 自分が起票した PR でも `gh pr merge --auto` を使う。auto-merge が有効化されない＝required check が通っていない、が本来の意味。
- 本番にデプロイしたものは必ずタグで復元できる状態を保つ（deployed = tagged）。→ 同 §5-4（ブランチ管理・リリースフロー標準）

### リリースフロー（ストア配布アプリ: iOS / Android）

→ 詳細: **リリースエンジニアリング標準**（運営リポ）（ストア配布アプリのリリース監査で制定・2026-07-16）。要点:

- **ビルド 1 段 + 人手ゲート 1 個**（テストした binary = 出荷する binary。外向き最終ステップだけ人手）
- リリース起点 workflow に **PR 選別手段**（draft 除外 / hold ラベル / include list）と**バージョン単調増加ガード**を必ず備える
- **version 未 bump の push が本番ビルドを発火させない**構造にする（配信 workflow のリリース subject 限定 + dependabot manifest 除外）
- 本番提出前に**ソーク + スモーク + クライアント承認の証跡**（受託）。App Store は Manually release + Phased Release
- **rollback runbook 必須**（revert は build 番号 +1 と同一 PR）
- **新規 PII 収集はコード完成 ≠ 出荷可**（ポリシー公開 + ストア申告 + GDPR opt-in が前提）

### リリースチェックリスト

**判定方法の正本は ブランチ管理・リリースフロー標準 §5-5（運営リポ） の機械判定表**。自己申告の checkbox ではなく、required check の緑 / コマンドの出力で判定する。

- [ ] テスト全通過（unit + E2E）
- [ ] Lintエラーゼロ
- [ ] 型チェック通過（`tsc --noEmit`）
- [ ] セキュリティチェック（code-review-checklist準拠）
- [ ] パフォーマンス基準達成（Lighthouse確認）
- [ ] CHANGELOG更新（Conventional Commitsベース）
- [ ] **Firestore rules / indexes の差分確認**（rules は Emulator の deny テストが緑であること）
- [ ] CDK diff確認（Route53 / SES に変更がある場合）
- [ ] **デプロイ後の live 検証**（`curl` で本番 URL / `gh run list` で deploy job 成否 / `aws amplify list-jobs`）
- [ ] （UI/DS 変更を含む場合）3 段 DoD — `scripts/ui-dod-gate.sh`
- [ ] （ストア配布）リリースエンジニアリング標準（運営リポ） のリリース前ブロッカー確認（ストア契約 / コンプラ申告 / テスターグループ / Secrets）

---

## 16. 見積り（**AI 前提が既定**・2026-09-08 新設）

**正本は本書ではなく 見積り skill `estimate-ai` と機械可読アンカー `estimate-anchors.json`（現在は運営リポ・M1 で本リポへ移す）。** 本節は方針と、開発側が守る 2 点だけを持つ。

### 方針

- **見積りは AI 活用を前提に出す。** 人手換算は「市場価格の参照値」としてのみ出し、**請求根拠には使わない**
- 3 手法（FP 法・類推法・積み上げ法）で出して相互検算する。1 手法だけだとその手法固有の癖がそのまま誤差になる

### 開発側が守る 2 点

1. **各 WBS 行の完了条件を機械で判定できる形で書く**（CI job 名 / exit code / 実測コマンド）。
   書けない行は AI 倍率を上げない。**「速い」と「終わった」は別**である
2. **作業が終わったら実績を戻す**（SKILL Step 14）。`assets/knowledge/effort-ledger.jsonl` と
   `estimate-anchors.json` の `calibration.samples` に 1 行ずつ。**`dod_evidence` の無いサンプルは採用されない**

### なぜ 2 が要るか（2026-09-08）

受託アプリ 1 件の初期段で **21h と見積もった作業が実測 1.52h（上界）で終わった**（13.8 倍の過大）。
問題は外したことではなく、**外したことに機械が気づけなかった**ことである。当時 `estimate-anchors.json` の
`hours_actual` は全件 null、`effort-ledger.jsonl` に該当案件の行は 0 行で、見積りと実績を突き合わせる経路が
どこにも無かった。⚠️ **倍率を勘で下げる是正はしない** — それは「実測しない」という原因をそのまま繰り返す。

週次 routine `estimate-accuracy`（`python3 scripts/estimate-accuracy.py`）が、
**AI 前提で見積もったのに実績が 1 行も無い案件**を名指しで出す（exit 1）。

> **merge は plan・live が state。** 上のチェックが全部埋まっても本番に反映されたことにはならない。live 検証を通すまで「完了」と言わない。

### ホットフィックス

→ 通常の短命ブランチと同じ経路（`fix/<slug>` → PR → main）。backmerge は行わない。詳細は ブランチ管理・リリースフロー標準 §2-1 / §3（運営リポ）。

---

## 15. Claude Code活用基準

→ 詳細: Claude Code 統合ガイド（運営リポ）

### Standards Overrides 仕様

各プロジェクトの `CLAUDE.md` に以下のセクションを追加し、本標準からの逸脱を明記:

```markdown
## Standards Overrides

| 項目 | 標準 | 本PJの選択 | 理由 |
|---|---|---|---|
| DB | DynamoDB | Firestore | Firebase 推奨 |
```
---

## Appendix: 関連文書

| 文書 | 所在 | 備考 |
|---|---|---|
| ブランチ管理・リリースフロー標準 | 運営リポ | 社内の決定記録。汎用部（命名 / タグ / 並行開発）は M1 で本リポへ |
| リリースエンジニアリング標準 | 運営リポ | §14 の詳細版（ストア配布アプリ） |
| 承認レベル | 運営リポ | 社内の決定記録 |
| コードレビューチェックリスト | 運営リポ | §3 / §12 の詳細 |
| Claude Code 統合ガイド | 運営リポ | §15 の詳細 |
| 見積り skill `estimate-ai` / `estimate-anchors.json` | 運営リポ | §16 の正本 |
| 成熟度の宣言（`maturity.json`） | 本リポ `scripts/maturity-check.py` | 配布物は成熟度表示が必須 |
| 秘匿ゲート | 本リポ `scripts/secrecy-check.py` | 顧客名 / 金額 / 内部台帳を**入る手前**で止める |

> ⚠️ **運営リポ側の文書名をここでリンクにしない。** 本リポは OSS へ export されるため、
> 参照は「名前」で持ち、パスや URL は持たない。

---

## 改定ルール（**2026-09-08 改訂**: 四半期 → 2 週間スプリント）

> 責任者 判断 2026-09-08:
> > D3: 2週間ごとのスプリント形式でのレビューにしますか。四半期だとモデル更改などもあるので遅すぎます。

**四半期レビューは機能しなかった。** 2026-08-04 の 1 行しか無く、しかも 2026-08-13（#1225）と 2026-09-01（#1629）の 2 回の改訂が変更履歴に載っていなかった（2026-09-08 の監査で発見・遡及記入）。**定期は守られない。赤は守られる。**

### 隔週スプリントレビュー（`standards-review` routine・偶数週の月曜）

1. **赤いゲートを分母にする**（時間でなく状態で駆動する）。**現時点でこの 4 本は運営リポで走る**
   （M1 で汎用部を本リポへ移すまで、レビューは運営リポ側で起動する）:

   | ゲート | 何を見るか |
   |---|---|
   | `estimate-accuracy` | 見積りが実績で校正されているか |
   | `repo-standard-drift --live` | 適用状態のドリフト |
   | `govern policy-drift` | 決定と文書の乖離 |
   | `bug-pattern-classify` | 同型の再発（fixture 候補） |

   exit 1 のものが**その回の改定候補**。緑だけの回は「レビュー・変更なし」で 1 行残す。
   ⚠️ **緑を「問題なし」と読まない** — ゲートが走っていない回も緑に見える。実行した証拠（検出件数）まで確認する
2. **モデル更改を明示的な入力にする** — Claude / Codex / ローカル LLM の版が上がったら、§1 のスタック・`model-effort-routing.md`・AI 倍率表（`estimate-anchors.json`）の 3 点を必ず見る。**これが四半期だと遅すぎるという 責任者 指摘の核心**
3. **変更履歴の各行に実測コマンドか PR 番号を必ず書く**（書けない改定は根拠が無いということ）

- 本標準の変更は **Level 1（即時実行）** — 社内ドキュメント更新に該当
- 重大な方針変更（技術スタック追加等）は **Level 3（事前承認）**
- ブランチ運用・リリースフローの改定は本書ではなく**ブランチ管理・リリースフロー標準**側で行う（本書は要約のみを持つ）

---

## 変更履歴

> 移送前（2026-03-18〜2026-09-08）の履歴は運営リポ側の同名ファイルに残っている。
> **本書はここから先を記録する。** 各行に実測コマンドか PR 番号を必ず書く（書けない改定は根拠が無いということ）。

| 日付 | 変更 |
|---|---|
| 2026-09-14 | **隔週スプリントレビュー 2 回目（`standards-review`・アンカー = 偶数 ISO 週の月曜・変更なし）**。初回と同じ結果を運営リポの sweep で再実測: 見積もり精度 exit 1（同一 5 件・倍率表は据え置き）/ リポ標準のずれ（live）exit 1（走査 38 本中 3 件 → 1 件は運営リポ側の Tier 登録で解消・残り 2 件は受託案件の段階 0 の未適用）/ 方針のずれ exit 0 / バグ型の分類 exit 1（313 件・同型 13 型・校正済 3 / 未校正 10）。モデル更改なし。本文は変えない |
| 2026-09-13 | **隔週スプリントレビュー初回（`standards-review`・変更なし）**。赤いゲート 3 本を改定候補として記録: 見積もり精度 exit 1（未校正の案件あり・M0 の 4 サンプルが 6.8〜45.2x 乖離・サンプルの偏りのため倍率表は据え置き）/ リポ標準のずれ（live）exit 1（38 本中 3 件: Tier 未割当 1・受託案件の段階 0 未適用 2）/ バグ型の分類 exit 1（未校正 10 型 / 13 型）。方針のずれ exit 0（advisory 6 件）。**モデル更改の入力なし**（§1 と effort・見積もりの基準は据え置き）。本文の改定は無し。記録は運営リポの週次の議事録 |
| 2026-09-09 | **運営リポから移送**。移送の判断根拠は実測 2 点 — 本書を機械で読むスクリプトが運営リポに **0 本**であること（＝移しても既存ゲートが 1 本も止まらない）と、公開前 redaction ゲートの検出が **24 件**（案件名・金額のみ・構造的な秘匿は無し）で一般化が小さいこと。移送にあたり ①§0 を「本書が持たないもの」表へ書き換え ②案件固有の記述 6 箇所を汎用化（§1-1 の実測根拠は「数えてから反転する」という手順として残した） ③プロジェクト別技術マッピング表（適用台帳）を運営リポに残置 ④相対リンクを全廃し参照を名前で持つ形へ。検証は `secrecy-check.py` と運営リポの `public-export-check.py` の両方で 0 件 |
