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
# Exit: 0 = healthy, 1 = problem found (actionable diagnosis printed).
# Portability: bash + python3 only (macOS dev + ubuntu CI).
set -uo pipefail

PLUGIN_ID="willink-claude-kit@iwillink"
CLAUDE_HOME="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
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
  "$PWD/.claude/settings.json" \
  "$PWD/.claude/settings.local.json"
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
if [ -f "$INSTALLED_JSON" ]; then
  INSTALL_PATH="$(json_get "$INSTALLED_JSON" "(d.get('plugins',{}).get('$PLUGIN_ID') or [{}])[0].get('installPath','')")"
  VER="$(json_get "$INSTALLED_JSON" "(d.get('plugins',{}).get('$PLUGIN_ID') or [{}])[0].get('version','')")"
  if [ -n "$INSTALL_PATH" ]; then
    c_ok "installed_plugins.json に登録あり (version=${VER:-unknown})"
  else
    c_bad "installed_plugins.json に $PLUGIN_ID が無い（未インストール）"
    printf '       fix: /plugin から marketplace 経由でインストール\n'
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
else
  c_warn "installPath 未確定のため中身検査をスキップ"
fi

# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
printf '\n========================================\n'
if [ "$PROBLEMS" -eq 0 ]; then
  printf '\033[32mHEALTHY\033[0m — 設定・実体ともに正常。\n'
  printf 'セッションに未反映の場合は Claude Code を再起動して /build が通るか確認する。\n'
  exit 0
fi

printf '\033[31m%d 件の問題を検出\033[0m — 上記 fix を適用後、Claude Code を再起動して反映する。\n' "$PROBLEMS"
printf '再起動しないと settings.json を直しても現セッションには反映されない。\n'
exit 1
