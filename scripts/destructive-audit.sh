#!/usr/bin/env bash
# =============================================================
# destructive-audit.sh — 破壊/env 変更コマンドの pre-bash-safety.sh カバレッジ監査（読取専用）
#
# 目的: 破壊系・env/self-lockout 系コマンド（rm -rf / force push / reset --hard /
#       git clean -f / mkfs / dd of=/dev/ / aws delete / DNS 変更 / runtime env 変更 /
#       secret rotation）の代表 canary を、既存の fail-closed フック
#       `.claude/hooks/pre-bash-safety.sh` に **read-only で通して** ブロックされるかを観測し、
#       ブロックされない＝gap（フックの穴）を検出して、追加候補パターンを **印字だけ** する。
#
# ⚠️ 重要な境界（原則 P3 / 安全制約）:
#   - 本スクリプトは pre-bash-safety.sh を **一切改変しない**（読むだけ / 実行して exit code を見るだけ）。
#   - gap を埋める hardening（フックへのパターン追加）は self-lockout に触れうる **Level 3**。
#     適用は **人手（責任者 承認後に 運用者 が手動）**。本スクリプトは候補を stdout に印字するのみ。
#   - harness gate ⊇ policy の原則: cloud 系 L3（aws delete/DNS/env/secret）は現状 policy(L3 承認)
#     だけで守られ、fail-closed gate には載っていない。gap 提案はこの差を可視化するためのもの。
#
# 使い方:
#   destructive-audit.sh [--hook <path>]
#       破壊/env canary を pre-bash-safety.sh に通し、covered/gap のマトリクスを印字。
#       gap があれば追加候補パターンを印字（適用はしない）。
#       exit 0 = 監査完了（gap 有無に関わらず観測できた）
#       exit 3 = フック不在/実行不能で観測できず（gap を 0 と誤読しない＝「不明」）
#
#   destructive-audit.sh --self-test
#       gh/aws/実フック非依存の hermetic テスト。temp の擬似フック fixture を作り、
#       probe→classify ロジックが「ブロックする category=covered / しない category=gap」を
#       正しく判定するかを検証。全ケース一致で exit 0、1 つでも不一致で exit 1（原則 P1）。
#
# 設計: BSD grep 互換（grep -P 不使用・[[:space:]] 使用）。JSON 組立は sed（jq/python 不要）。
#       実フック側の JSON パースは pre-bash-safety.sh 自身（jq or python3）に委ねる。
# =============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_phroot.sh
. "$SCRIPT_DIR/_phroot.sh"
REPO_ROOT="$(ph_target_root)"
HARNESS_HOME="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEFAULT_HOOK="$REPO_ROOT/.claude/hooks/pre-bash-safety.sh"

# ---- 純粋関数: command 文字列を Claude Code hook JSON に組み立てる --------
# canary は ASCII（/, 空白, ~, . のみ）想定。\ と " のみエスケープすれば十分。
build_json() {
  esc=$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')
  printf '{"tool_input":{"command":"%s"}}' "$esc"
}

# ---- 純粋関数: フックの exit code → 判定ラベル --------------------------
# 2 = ブロック(covered) / 0 = 素通り(gap) / それ以外 = unknown（0 件扱いしない）
verdict_from_exit() {
  case "$1" in
    2) echo "covered" ;;
    0) echo "gap" ;;
    *) echo "unknown" ;;
  esac
}

# ---- 純粋関数: canary を対象フックに read-only で通し exit code を返す ----
# フックは stdin JSON を読み、危険なら exit 2。実行しても副作用は無い（read のみ）。
probe_exit() {
  _hook="$1"; _cmd="$2"
  build_json "$_cmd" | bash "$_hook" >/dev/null 2>&1
  echo "$?"
}

# ---- 純粋関数: フック source に category の痕跡があるか（静的 read 監査）--
# 返り値: 0=痕跡あり / 1=痕跡なし。
static_has_pattern() {
  _hook="$1"; _sig="$2"
  # `--` = end-of-options guard so signatures starting with `--`（--force / --no-verify）
  # are treated as patterns, not grep flags.
  grep -qE -- "$_sig" "$_hook" 2>/dev/null
}

# ---- category テーブル（並列配列・bash3.2 互換）-------------------------
# CAT_ID / 表示名 / canary command / source 内 signature(grep) / gap 時の追加候補パターン
CAT_ID=(     rm-rf         force-push          reset-hard              git-clean            mkfs        dd-dev        no-verify        aws-delete   dns-change   runtime-env  secret-rotate )
CAT_LABEL=(  "rm -rf 破壊" "force push main"   "git reset --hard"      "git clean -f"       "mkfs"      "dd of=/dev/" "commit --no-verify" "aws 削除"   "DNS 変更"   "runtime env 変更" "secret rotation" )
CAT_CANARY=( "rm -rf /"    "git push --force origin main" "git reset --hard HEAD~3" "git clean -fd" "mkfs.ext4 /dev/sda1" "dd if=/dev/zero of=/dev/sda" "git commit --no-verify -m x" "aws dynamodb delete-table --table-name prod" "aws route53 change-resource-record-sets --hosted-zone-id Z123" "aws amplify update-app --app-id d1 --environment-variables KEY=val" "aws secretsmanager rotate-secret --secret-id prod/db" )
CAT_SIG=(    "rm\[\[:space:\]\]" "--force" "reset\[\[:space:\]\]\+.\*--hard" "git\[\[:space:\]\]\+clean" "mkfs" "of=/dev/" "--no-verify" "aws\[\[:space:\]\]\+.\*delete" "route53" "amplify\[\[:space:\]\]\+update-app" "rotate-secret" )
CAT_PROP=(   "-" "-" "-" "-" "-" "-" "-" \
  "(^|[[:space:]])aws[[:space:]]+([^;|&]*[[:space:]])?(delete-[a-z-]+|rb[[:space:]]|s3[[:space:]]+rb)" \
  "(^|[[:space:]])aws[[:space:]]+route53[[:space:]]+change-resource-record-sets" \
  "(^|[[:space:]])aws[[:space:]]+(amplify[[:space:]]+update-app|lambda[[:space:]]+update-function-configuration)[[:space:]][^;|&]*(--environment-variables|--environment)" \
  "(^|[[:space:]])aws[[:space:]]+secretsmanager[[:space:]]+(rotate-secret|put-secret-value|delete-secret)" )

# ===========================================================================
# 監査モード
# ===========================================================================
audit_mode() {
  hook="$1"
  echo "── destructive/env カバレッジ監査（read-only・フックを改変しない）──"
  echo "   対象フック: $hook"

  if [ ! -f "$hook" ]; then
    echo "  ❌ pre-bash-safety.sh が見つからない → カバレッジ『不明』（gap を 0 と誤読しない）" >&2
    return 3
  fi

  printf '\n  %-14s %-10s %-9s %s\n' "category" "probe" "source" "canary"
  printf '  %-14s %-10s %-9s %s\n' "--------" "-----" "------" "------"

  gap_idx=""
  unknown=0
  i=0
  n=${#CAT_ID[@]}
  while [ "$i" -lt "$n" ]; do
    code=$(probe_exit "$hook" "${CAT_CANARY[$i]}")
    verd=$(verdict_from_exit "$code")
    if static_has_pattern "$hook" "${CAT_SIG[$i]}"; then
      src="有"
    else
      src="無"
    fi
    printf '  %-14s %-10s %-9s %s\n' "${CAT_ID[$i]}" "$verd(rc=$code)" "$src" "${CAT_CANARY[$i]}"
    case "$verd" in
      gap)     gap_idx="$gap_idx $i" ;;
      unknown) unknown=1 ;;
    esac
    i=$((i + 1))
  done

  echo ""
  if [ -z "$gap_idx" ] && [ "$unknown" -eq 0 ]; then
    echo "  ✅ 全 destructive/env category が fail-closed gate でカバー済み。gap なし。"
    return 0
  fi

  if [ "$unknown" -eq 1 ]; then
    echo "  ⚠️ unknown(rc≠0,2) を含む → 該当 category は『不明』。covered とは扱わない。"
  fi

  if [ -n "$gap_idx" ]; then
    print_gap_proposal "$gap_idx"
  fi
  return 0
}

# ---- gap 提案の印字（印字のみ・適用は人手/L3）--------------------------
print_gap_proposal() {
  _gaps="$1"
  cat <<'EOF'

────────────────────────────────────────────────────────────
【gap 検出】 上記 gap category は pre-bash-safety.sh で未ブロック。
【承認レベル】 これらは cloud self-lockout 相当（DNS/runtime env/secret/破壊）。
  現状は policy(Level 3 = 責任者 事前承認) のみで守られ、fail-closed gate には未搭載。
  ▶ フックへのパターン追加(=hardening)は self-lockout に触れうる Level 3。
  ▶ 本スクリプトは **候補パターンを印字するだけ**。適用は 責任者 承認後に 運用者 が手動で行う。
     （harness gate ⊇ policy を満たす方向の提案。gate を policy で緩めてはいけない。）
────────────────────────────────────────────────────────────
以下は pre-bash-safety.sh に追加を **検討** できる grep -E パターン候補（BSD 互換）:
EOF
  for idx in $_gaps; do
    prop="${CAT_PROP[$idx]}"
    [ "$prop" = "-" ] && continue
    printf '\n  # [%s] %s\n' "${CAT_ID[$idx]}" "${CAT_LABEL[$idx]}"
    printf "  if printf '%%s' \"\$SCAN_CMD\" | grep -qE '%s'; then\n" "$prop"
    printf '    block "%s は self-lockout/破壊の恐れ（Level 3）。" "責任者 承認後に手動実行。"\n' "${CAT_LABEL[$idx]}"
    printf '  fi\n'
  done
  cat <<'EOF'

  ※ 上記はあくまで候補。過検出(false positive)で正当な運用を止めない設計・
    test-hooks.sh への block/pass 両ケース追加・責任者 承認を経てから適用すること。
────────────────────────────────────────────────────────────
EOF
}

# ===========================================================================
# self-test（hermetic・実フック/gh/aws 非依存）
# ===========================================================================
self_test() {
  echo "── destructive-audit self-test（hermetic・probe+classify 検証）──"
  fail=0

  # --- (A) verdict_from_exit の写像を直接検証 ---
  check_map() {
    _in="$1"; _exp="$2"
    _got=$(verdict_from_exit "$_in")
    if [ "$_got" = "$_exp" ]; then
      echo "  ✅ verdict($_in)=$_got"
    else
      echo "  ❌ verdict($_in)=$_got（期待 $_exp）"; fail=1
    fi
  }
  check_map 2 covered
  check_map 0 gap
  check_map 1 unknown
  check_map 3 unknown

  # --- (B) 擬似フック fixture を作り probe_exit の end-to-end を検証 ---
  # fixture は「rm -rf / と git reset --hard だけをブロック(exit 2)」する部分フック。
  # → これらの category は covered、それ以外(aws/dns/env)は gap と判定されるべき。
  tmpdir=$(mktemp -d -t destaudit.XXXXXX) || { echo "  ❌ mktemp 失敗"; return 1; }
  trap 'rm -rf "$tmpdir"' EXIT
  fixture="$tmpdir/fake-hook.sh"
  cat > "$fixture" <<'FIX'
#!/usr/bin/env bash
# hermetic 擬似フック: stdin(JSON) を受け、危険部分列があれば exit 2、無ければ 0。
set -uo pipefail
input=$(cat)
if printf '%s' "$input" | grep -qE 'rm[[:space:]]+-rf[[:space:]]+/'; then exit 2; fi
if printf '%s' "$input" | grep -qE 'git[[:space:]]+reset[[:space:]]+--hard'; then exit 2; fi
exit 0
FIX
  chmod +x "$fixture"

  # 期待表: "canary<TAB>期待verdict"
  probe_case() {
    _cmd="$1"; _exp="$2"
    _code=$(probe_exit "$fixture" "$_cmd")
    _got=$(verdict_from_exit "$_code")
    if [ "$_got" = "$_exp" ]; then
      echo "  ✅ probe '$_cmd' → $_got"
    else
      echo "  ❌ probe '$_cmd' → $_got（期待 $_exp, rc=$_code）"; fail=1
    fi
  }
  probe_case "rm -rf /"                      covered   # fixture がブロック
  probe_case "git reset --hard HEAD~3"       covered   # fixture がブロック
  probe_case "aws dynamodb delete-table --table-name p" gap  # fixture は素通り＝穴
  probe_case "aws route53 change-resource-record-sets"  gap
  probe_case "aws amplify update-app --environment-variables K=v" gap

  # --- (C) build_json が JSON をこわさず command を運べる（空白入り）---
  j=$(build_json "git reset --hard HEAD~3")
  if printf '%s' "$j" | grep -qF '"command":"git reset --hard HEAD~3"'; then
    echo "  ✅ build_json roundtrip OK"
  else
    echo "  ❌ build_json 破損: $j"; fail=1
  fi

  # --- (D) static_has_pattern が fixture source を読めている ---
  if static_has_pattern "$fixture" 'reset\[\[:space:\]\]\+--hard'; then
    echo "  ✅ static_has_pattern hit（source read OK）"
  else
    echo "  ❌ static_has_pattern miss"; fail=1
  fi
  if static_has_pattern "$fixture" 'route53'; then
    echo "  ❌ static_has_pattern が無い痕跡を hit（false positive）"; fail=1
  else
    echo "  ✅ static_has_pattern 不在を正しく miss"
  fi

  if [ "$fail" -eq 0 ]; then
    echo "✅ SELF-TEST PASS（probe+classify+static+json 健全）"
    return 0
  fi
  echo "❌ SELF-TEST FAIL"
  return 1
}

# ===========================================================================
# 引数パース
# ===========================================================================
HOOK="$DEFAULT_HOOK"; MODE="audit"
while [ $# -gt 0 ]; do
  case "$1" in
    --hook)      HOOK="${2:-}"; shift 2 ;;
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

audit_mode "$HOOK"
exit $?
