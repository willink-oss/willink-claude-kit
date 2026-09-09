#!/usr/bin/env bash
# =============================================================
# required-check-audit.sh — branch protection の CI required check 監査（読取専用）
#
# 目的: 対象リポ/ブランチに「required status check」が何本設定されているかを
#       gh api の GET だけで観測し、0 本 or 未保護なら『required check 未導入』と
#       判定して、責任者 承認後に 運用者 が実行すべき適用コマンド例を stdout に印字する。
#
# ⚠️ 重要な境界（原則 P3）:
#   branch protection の「適用」は self-lockout 相当の Level 3。
#   本スクリプトは監査＋提案までで、protection を絶対に「適用しない」。
#   gh api は GET（read）のみ。PUT/POST/DELETE は一切実行しない（例示は印字のみ）。
#
# 使い方:
#   required-check-audit.sh --repo <owner/name> --branch <branch>
#     → GET repos/OWNER/NAME/branches/BRANCH/protection の
#       required_status_checks.contexts 本数を報告。
#       0 本 or 404(未保護) → 「未導入」判定 + 適用コマンド例を印字（実行しない）。
#       exit 0 = 監査成功（導入/未導入いずれも観測できた）
#       exit 2 = 引数不足
#       exit 3 = access 不能（404 以外の失敗。空出力を 0 件と誤読しない＝「不明」）
#
#   required-check-audit.sh --self-test
#     → gh に依存せず、内蔵 fixture(JSON) で
#       「contexts 本数の抽出 → 0→未導入 / >=1→導入済 の分類」ロジックを検証。
#       全ケース一致で exit 0、1 つでも不一致で exit 1（原則 P1 自己申告禁止）。
#
# 設計: JSON パースは python3（jq 非依存）。BSD grep 互換（grep -P 不使用）。
# =============================================================
set -uo pipefail

# ---- 純粋関数: JSON(stdin) から required check 名の本数を数える ----------
# 出力: 整数（>=0）。JSON パース不能なら -2 のセンチネル。
# required_status_checks.contexts（旧） と .checks[].context（新）を和集合で数える。
count_contexts() {
  python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print(-2); sys.exit(0)
rsc = d.get("required_status_checks")
if not rsc:
    print(0); sys.exit(0)
names = set(rsc.get("contexts") or [])
for c in (rsc.get("checks") or []):
    if isinstance(c, dict) and c.get("context"):
        names.add(c["context"])
print(len(names))
'
}

# ---- 純粋関数: 本数 → 判定ラベル ---------------------------------------
# n>=1 → 導入済 / n==0 → 未導入
classify_contexts() {
  n="$1"
  if [ "$n" -ge 1 ] 2>/dev/null; then
    echo "導入済"
  else
    echo "未導入"
  fi
}

# ---- 適用コマンド例の印字（実行はしない・責任者 承認後に 運用者 が手で叩く）----
print_apply_hint() {
  repo="$1"; branch="$2"
  cat <<EOF

────────────────────────────────────────────────────────────
【判定】 required check 未導入（contexts=0 または未保護）
【承認レベル】 Level 3 = 適用は 責任者 事前承認必須（self-lockout / 原則 P3）
  本スクリプトは適用しません。以下は承認後に 運用者 が手動で実行するコマンド例です。
────────────────────────────────────────────────────────────

# 1) まず対象ブランチで走っている check 名を実測（GET のみ）:
gh api "repos/${repo}/commits/${branch}/check-runs" \\
  --jq '.check_runs[].name' | sort -u

# 2) 責任者 承認後、required にしたい check 名を contexts に列挙して適用（PUT）:
#    ※ 下記は例。<check-name> を 1) の実名に置換すること。
gh api -X PUT "repos/${repo}/branches/${branch}/protection" \\
  -H "Accept: application/vnd.github+json" \\
  --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["<check-name>"] },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null
}
JSON

# 3) 適用後に本スクリプトを再実行し、contexts>=1（導入済）を実測で確認する。
────────────────────────────────────────────────────────────
EOF
}

# ---- self-test: gh 非依存で抽出+分類ロジックを内蔵 fixture で検証 --------
self_test() {
  echo "── required-check-audit self-test（gh 非依存・ロジック検証）──"
  fail=0

  # fixture: "JSON<TAB>期待本数<TAB>期待ラベル"
  run_case() {
    label="$1"; json="$2"; exp_n="$3"; exp_v="$4"
    got_n=$(printf '%s' "$json" | count_contexts)
    got_v=$(classify_contexts "$got_n")
    if [ "$got_n" = "$exp_n" ] && [ "$got_v" = "$exp_v" ]; then
      echo "  ✅ $label : contexts=$got_n → $got_v"
    else
      echo "  ❌ $label : contexts=$got_n(期待$exp_n) → $got_v(期待$exp_v)"
      fail=1
    fi
  }

  # 1) 未保護相当（required_status_checks: null）→ 0 → 未導入
  run_case "null(未保護相当)" '{"required_status_checks": null}' 0 "未導入"
  # 2) 空 contexts → 0 → 未導入
  run_case "空contexts"      '{"required_status_checks":{"strict":true,"contexts":[]}}' 0 "未導入"
  # 3) contexts 2 本 → 2 → 導入済
  run_case "contexts2本"     '{"required_status_checks":{"strict":true,"contexts":["ci/build","ci/test"]}}' 2 "導入済"
  # 4) 新 checks 形式 1 本 → 1 → 導入済
  run_case "checks形式1本"   '{"required_status_checks":{"contexts":[],"checks":[{"context":"lint","app_id":null}]}}' 1 "導入済"
  # 5) contexts と checks の和集合（重複除去）→ 2 → 導入済
  run_case "和集合重複除去"   '{"required_status_checks":{"contexts":["a","b"],"checks":[{"context":"a"}]}}' 2 "導入済"
  # 6) protection キー自体なし（{} = 未保護 GET が {} を返す想定）→ 0 → 未導入
  run_case "空オブジェクト"   '{}' 0 "未導入"

  if [ "$fail" -eq 0 ]; then
    echo "✅ SELF-TEST PASS（抽出+分類ロジック健全）"
    return 0
  fi
  echo "❌ SELF-TEST FAIL"
  return 1
}

# ---- 引数パース --------------------------------------------------------
REPO=""; BRANCH=""; MODE="audit"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo)   REPO="${2:-}"; shift 2 ;;
    --branch) BRANCH="${2:-}"; shift 2 ;;
    --self-test) MODE="self-test"; shift ;;
    -h|--help)
      grep -E '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ "$MODE" = "self-test" ]; then
  self_test
  exit $?
fi

# ---- 監査モード（gh api GET のみ）--------------------------------------
if [ -z "$REPO" ] || [ -z "$BRANCH" ]; then
  echo "usage: required-check-audit.sh --repo <owner/name> --branch <branch>" >&2
  echo "       required-check-audit.sh --self-test" >&2
  exit 2
fi

command -v gh >/dev/null 2>&1 || { echo "gh CLI が必要です（監査モード）" >&2; exit 3; }

echo "── required check 監査: repo=${REPO} branch=${BRANCH}（GET のみ・適用しない）──"

err_file="$(mktemp -t rca_err.XXXXXX)"
# GET（-X 未指定 = GET）。protection が無いと 404。
raw=$(gh api "repos/${REPO}/branches/${BRANCH}/protection" 2>"$err_file")
rc=$?

if [ "$rc" -ne 0 ]; then
  # 404(=未保護) と access 不能(401/403/その他)を区別する。空出力を 0 件と誤読しない。
  if grep -qE '404|Not Found|Branch not protected' "$err_file"; then
    rm -f "$err_file"
    echo "  protection: 未設定（404 = ブランチ未保護）"
    echo "  contexts=0 → $(classify_contexts 0)"
    print_apply_hint "$REPO" "$BRANCH"
    exit 0
  fi
  echo "  ❌ access 不能（404 以外の失敗）: $(tr '\n' ' ' < "$err_file")" >&2
  echo "  → 状態は『不明』。0 件とは扱わない（common-mistakes: 空出力≠ゼロ件）。" >&2
  rm -f "$err_file"
  exit 3
fi
rm -f "$err_file"

n=$(printf '%s' "$raw" | count_contexts)
if [ "$n" = "-2" ]; then
  echo "  ❌ JSON パース不能 → 状態は『不明』" >&2
  exit 3
fi

verdict=$(classify_contexts "$n")
echo "  required_status_checks.contexts = ${n} 本 → ${verdict}"

if [ "$n" -ge 1 ]; then
  echo "  ✅ CI required check 導入済（KPI 目標『0→増』達成側）。監査完了。"
  exit 0
fi

# 0 本（保護はあるが required check なし）→ 未導入扱いで提案印字
print_apply_hint "$REPO" "$BRANCH"
exit 0
