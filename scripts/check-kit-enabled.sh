#!/usr/bin/env bash
# scripts/check-kit-enabled.sh — "is the kit actually LOADED?" doctor.
#
# Why this exists: the kit can be fully installed on disk and shown as enabled in
# /plugin while loading nothing at all. The known trigger is an `enabledPlugins`
# value written as an array (`["2.2.0"]`) instead of the boolean `true` — Claude Code
# writes `true` when it enables a plugin, and the array-only form has been observed
# to leave the plugin listed-but-dead. It fails silently: no error, no warning, and
# /plugin still says "enabled", so nobody notices the kit stopped working.
# See docs/failure-modes.md #11.
#
# This script separates two things the UI conflates:
#   INSTALLED = files are on disk        LOADED = Claude Code will actually register them
#
# 検査（2026-09-26 に実物へ合わせて更新）:
#   [1] enabledPlugins の値型（boolean true か）— user / project（git のルート）/ local
#   [2] インストール実体 — installed_plugins.json を **このリポの project scope → user scope** の順に引く
#       （先頭の記録は別リポの古い project scope のことがある）。版数を marketplace の複製と比べる
#   [3] ロード対象 — commands / agents / skills の件数・hooks/*.sh の実行可否・**hooks/hooks.json の登録本数と参照先**
#       （2.7.0 以降 hook は hooks.json で登録される。置いてあるだけでは 1 本も登録されない）
#   [4] 止める hook の実挙動 — pre-file-protect.sh に `.env` への Write を渡して exit 2、普通のファイルで exit 0
#       （hook に入力を渡すだけで、ファイルは作らない）
#
# Exit: 0 = healthy, 1 = problem found (actionable diagnosis printed).
# Portability: bash + python3 only (macOS dev + ubuntu CI).
set -uo pipefail

PLUGIN_ID="willink-claude-kit@iwillink"
MARKETPLACE_REPO="willink-oss/willink-claude-kit"
CLAUDE_HOME="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
# project の設定は git のルートで読む（サブディレクトリから実行しても同じ結果にする）
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PROBLEMS=0

c_ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
c_bad()  { printf '  \033[31mNG\033[0m   %s\n' "$1"; PROBLEMS=$((PROBLEMS + 1)); }
c_warn() { printf '  \033[33m??\033[0m   %s\n' "$1"; }

# json_get <file> <python-expr over `d`> — print result, empty on any failure.
json_get() {
  python3 -c '
import json,sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
try:
    # `json` must be in scope: callers pass expressions like json.dumps(...).
    v = eval(sys.argv[2], {"json": json}, {"d": d})
except Exception:
    sys.exit(1)
print("" if v is None else v)
' "$1" "$2" 2>/dev/null
}

printf '\n=== willink-claude-kit doctor ===\n'
printf 'plugin: %s\n' "$PLUGIN_ID"

# ---------------------------------------------------------------------------
# 1. enabledPlugins の値型 — 今回の silent-disable の本丸
# ---------------------------------------------------------------------------
printf '\n[1] enabledPlugins の値型\n'

FOUND_SCOPE=""
for scope_file in \
  "$CLAUDE_HOME/settings.json" \
  "$PROJECT_ROOT/.claude/settings.json" \
  "$PROJECT_ROOT/.claude/settings.local.json"
do
  [ -f "$scope_file" ] || continue

  raw="$(json_get "$scope_file" "json.dumps(d.get('enabledPlugins',{}).get('$PLUGIN_ID','__ABSENT__'))")"
  [ -n "$raw" ] || continue
  [ "$raw" = '"__ABSENT__"' ] && continue

  FOUND_SCOPE="$scope_file"
  kind="$(json_get "$scope_file" "type(d.get('enabledPlugins',{}).get('$PLUGIN_ID')).__name__")"

  case "$raw" in
    true)
      c_ok "$scope_file → true (boolean) — 正しい有効化形式"
      ;;
    false)
      c_bad "$scope_file → false — 明示的に無効化されている"
      printf '       fix: \"%s\": true に変更\n' "$PLUGIN_ID"
      ;;
    \[*)
      c_bad "$scope_file → $raw (array) — /plugin 上は「有効」でも一切ロードされない"
      printf '       fix: \"%s\": true に変更（array 単独では有効化されない）\n' "$PLUGIN_ID"
      printf '       バージョン固定は marketplace の source.ref で行う。docs/failure-modes.md #11\n'
      ;;
    *)
      c_bad "$scope_file → $raw ($kind) — boolean ではない不正な値型"
      printf '       fix: \"%s\": true に変更\n' "$PLUGIN_ID"
      ;;
  esac
done

if [ -z "$FOUND_SCOPE" ]; then
  c_bad "どの settings.json にも $PLUGIN_ID の宣言が無い（未有効化）"
  printf '       fix: .claude/settings.json の enabledPlugins に \"%s\": true を追加\n' "$PLUGIN_ID"
fi

# ---------------------------------------------------------------------------
# 2. インストール実体
# ---------------------------------------------------------------------------
printf '\n[2] インストール実体\n'

INSTALLED_JSON="$CLAUDE_HOME/plugins/installed_plugins.json"
INSTALL_PATH=""
# 記録はこのリポの project scope → user scope の順に引く（先頭の記録は別のリポの project scope のことがある。
# 2026-09-26: 別リポの 2.5.0 を先頭で拾い、このリポでは 2.9.0 が効いているのに「旧版」と誤診した形を塞ぐ）
pick_entry() {  # $1 = 取り出す key
  python3 - "$INSTALLED_JSON" "$PLUGIN_ID" "$PROJECT_ROOT" "$1" <<'PY' 2>/dev/null
import json, os, sys
path, pid, root, key = sys.argv[1:5]
try:
    es = json.load(open(path)).get("plugins", {}).get(pid) or []
except Exception:
    sys.exit(0)
root = os.path.realpath(root)
e = next((e for e in es if e.get("scope") == "project" and os.path.realpath(e.get("projectPath") or "") == root), None) \
    or next((e for e in es if e.get("scope") in ("user", None)), None)
print((e or {}).get(key, "") or "")
PY
}
if [ -f "$INSTALLED_JSON" ]; then
  INSTALL_PATH="$(pick_entry installPath)"
  VER="$(pick_entry version)"
  SCOPE="$(pick_entry scope)"
  if [ -n "$INSTALL_PATH" ]; then
    c_ok "installed_plugins.json に登録あり (scope=${SCOPE:-user} / version=${VER:-unknown})"
  else
    c_bad "installed_plugins.json に、このリポ（project）でもユーザー全体（user）でも $PLUGIN_ID の記録が無い（未インストール）"
    printf '       fix: claude plugin marketplace add %s\n' "$MARKETPLACE_REPO"
    printf '            claude plugin install %s --scope project   # まっさらな機では add を先に打たないと install は失敗する\n' "$PLUGIN_ID"
  fi
else
  c_warn "installed_plugins.json が無い: $INSTALLED_JSON"
fi

if [ -n "$INSTALL_PATH" ]; then
  if [ -d "$INSTALL_PATH" ]; then
    c_ok "installPath 実在: $INSTALL_PATH"
  else
    c_bad "installPath が存在しない: $INSTALL_PATH"
    printf '       fix: /plugin で一度 uninstall → 再 install\n'
  fi
fi

# インストール済み版数 vs marketplace が配っている版数。
#
# なぜ要るか（2026-09-10 に 2 度目を踏んだ）: main に merge しても
# `marketplace.json` の `source.ref` を上げなければ、導入先には旧版が居座る。
# 旧版は**それ自体としては健全**なので、この doctor は HEALTHY を返してしまう。
# 実際に v2.5.0 のまま「skills 17 件・HEALTHY」と出しながら、公開済みの
# hook 14 本・engine 13 本・skill 26 本が 1 つも入っていない状態を見た。
# **「壊れている」と「古い」は別の診断**なので、別の行で出す。
#
# 比較するのは **手元の 2 つ**（installed_plugins.json と marketplace のローカル複製）。
# ネットワークは見ないので、複製自体が古い場合は検出できない。読めなければ
# `??` にする（**不明を OK と書かない**）。
MP_JSON="$CLAUDE_HOME/plugins/marketplaces/iwillink/.claude-plugin/marketplace.json"
if [ -f "$MP_JSON" ]; then
  MP_VER="$(json_get "$MP_JSON" "next((p.get('version','') for p in d.get('plugins',[]) if p.get('name')=='willink-claude-kit'), '')")"
  if [ -z "$MP_VER" ]; then
    c_warn "marketplace.json から version を読めない（0 件ではなく不明）: $MP_JSON"
  elif [ -z "${VER:-}" ]; then
    c_warn "installed version が不明のため版数を比較できない"
  elif [ "$MP_VER" = "$VER" ]; then
    c_ok "版数一致: installed=$VER / marketplace=$MP_VER"
  else
    c_bad "installed=$VER だが marketplace は $MP_VER — **旧版がロードされている**"
    printf '       壊れてはいないが古い。新しい版の commands / skills / hooks は 1 つも入っていない。\n'
    printf '       fix: /plugin から marketplace を update → willink-claude-kit を再 install → Claude Code 再起動\n'
  fi
else
  c_warn "marketplace のローカル複製が無い（版数を比較できない）: $MP_JSON"
fi

# ---------------------------------------------------------------------------
# 3. ロード対象の中身（コマンド / エージェント / スキル）
# ---------------------------------------------------------------------------
printf '\n[3] ロード対象の中身\n'

if [ -n "$INSTALL_PATH" ] && [ -d "$INSTALL_PATH" ]; then
  if [ -f "$INSTALL_PATH/.claude-plugin/plugin.json" ]; then
    if python3 -c 'import json,sys; json.load(open(sys.argv[1]))' \
        "$INSTALL_PATH/.claude-plugin/plugin.json" 2>/dev/null; then
      c_ok "plugin.json は valid JSON"
    else
      c_bad "plugin.json が壊れている（parse 不能）— プラグイン全体がロードされない"
    fi
  else
    c_bad "plugin.json が無い: $INSTALL_PATH/.claude-plugin/plugin.json"
  fi

  for d in commands agents skills; do
    if [ -d "$INSTALL_PATH/$d" ]; then
      n="$(find "$INSTALL_PATH/$d" -maxdepth 1 -mindepth 1 | wc -l | tr -d ' ')"
      c_ok "$d/ … $n 件"
    else
      c_bad "$d/ が無い"
    fi
  done

  # hooks は 2.6.0 で初搭載。**数えていなければ「配られていない」に気づけない**。
  # 実行可能でない hook は数から除く（置いてあるだけでは効かない）。
  if [ -d "$INSTALL_PATH/hooks" ]; then
    nh="$(find "$INSTALL_PATH/hooks" -maxdepth 1 -name '*.sh' -type f | wc -l | tr -d ' ')"
    nx="$(find "$INSTALL_PATH/hooks" -maxdepth 1 -name '*.sh' -type f -perm -u+x | wc -l | tr -d ' ')"
    if [ "$nh" -eq 0 ]; then
      c_bad "hooks/ が空（.sh が 0 本）"
    elif [ "$nx" -ne "$nh" ]; then
      c_bad "hooks/ … $nh 本のうち実行可能 $nx 本（$((nh - nx)) 本に +x が無い）"
      printf '       fix: chmod +x "%s"/hooks/*.sh\n' "$INSTALL_PATH"
    else
      c_ok "hooks/ … $nh 本（すべて実行可能）"
    fi
    # 2.7.0 以降、hook は hooks/hooks.json で plugin として登録される（settings.json に書く必要は無い）。
    # **置いてあるだけでは 1 本も登録されない**（2.6.0 は hooks.json 無しで 14 本を配り実効 0 本だった）ので、
    # hooks.json の登録本数と、参照先が実在して実行可能かを数える。
    if [ -f "$INSTALL_PATH/hooks/hooks.json" ]; then
      reg="$(python3 - "$INSTALL_PATH/hooks" <<'PY' 2>/dev/null
import json, os, re, sys
hd = sys.argv[1]
try:
    d = json.load(open(os.path.join(hd, "hooks.json")))
except Exception:
    print("PARSE"); sys.exit(0)
n = miss = 0
for ev in (d.get("hooks") or {}).values():
    for e in ev:
        for h in e.get("hooks", []):
            n += 1
            m = re.search(r"/hooks/([A-Za-z0-9._-]+\.sh)", h.get("command", ""))
            f = os.path.join(hd, m.group(1)) if m else ""
            if not (f and os.path.isfile(f) and os.access(f, os.X_OK)):
                miss += 1
print("%d %d" % (n, miss))
PY
)"
      case "$reg" in
        PARSE|"") c_bad "hooks/hooks.json が壊れている（parse 不能）— hook が 1 本も登録されない" ;;
        *)
          n_reg="${reg%% *}"; n_miss="${reg##* }"
          if [ "$n_reg" -eq 0 ]; then
            c_bad "hooks/hooks.json の登録が 0 本 — hook が 1 本も効かない"
          elif [ "$n_miss" -gt 0 ]; then
            c_bad "hooks/hooks.json … 登録 $n_reg 本のうち $n_miss 本の参照先が無い / 実行できない"
          else
            c_ok "hooks/hooks.json … $n_reg 本を登録（plugin を有効にすると Claude Code が登録する・settings.json に書く必要は無い）"
          fi ;;
      esac
    else
      c_bad "hooks/hooks.json が無い — hooks/*.sh を同梱していても 1 本も登録されない（2.7.0 以降の版に上げる）"
    fi
  else
    c_bad "hooks/ が無い — 2.6.0 以降で同梱。上の版数比較を確認する"
  fi
else
  c_warn "installPath 未確定のため中身検査をスキップ"
fi

# ---------------------------------------------------------------------------
# 4. 止める hook が本当に止めるか（安全な入力を 1 つずつ渡す・ファイルは作らない）
# ---------------------------------------------------------------------------
# 「登録されている」と「止める」は別。`.env` への Write を渡して exit 2、普通のファイルで exit 0 を見る。
# hook に渡すだけで Write は実行されない。
printf '\n[4] 止める hook の実挙動\n'
PROTECT="${INSTALL_PATH:+$INSTALL_PATH/hooks/pre-file-protect.sh}"
if [ -n "$PROTECT" ] && [ -x "$PROTECT" ]; then
  PROBE_DIR="$(mktemp -d)"
  printf '{"tool_name":"Write","tool_input":{"file_path":"%s/.env"}}' "$PROBE_DIR" \
    | CLAUDE_PROJECT_DIR="$PROBE_DIR" bash "$PROTECT" >/dev/null 2>&1; rb=$?
  printf '{"tool_name":"Write","tool_input":{"file_path":"%s/notes.md"}}' "$PROBE_DIR" \
    | CLAUDE_PROJECT_DIR="$PROBE_DIR" bash "$PROTECT" >/dev/null 2>&1; rp=$?
  rm -rf "$PROBE_DIR"
  if [ "$rb" -eq 2 ] && [ "$rp" -eq 0 ]; then
    c_ok "pre-file-protect.sh … .env への Write を止め（exit 2）、普通のファイルは通す（exit 0）"
  else
    c_bad "pre-file-protect.sh が期待どおりに動かない（.env → exit ${rb} / notes.md → exit ${rp}。期待 2 / 0）"
  fi
else
  c_warn "pre-file-protect.sh が見つからない / 実行できない — 実挙動は測れていない（0 件ではなく不明）"
fi

# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
printf '\n========================================\n'
if [ "$PROBLEMS" -eq 0 ]; then
  printf '\033[32mHEALTHY\033[0m — 設定・実体ともに正常。\n'
  printf 'セッションに未反映の場合は Claude Code を再起動して /build が通るか確認する（hook は再起動で読み込まれる）。\n'
  exit 0
fi

printf '\033[31m%d 件の問題を検出\033[0m — 上記 fix を適用後、Claude Code を再起動して反映する。\n' "$PROBLEMS"
printf '再起動しないと settings.json を直しても現セッションには反映されない。\n'
exit 1
