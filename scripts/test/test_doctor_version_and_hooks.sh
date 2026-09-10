#!/usr/bin/env bash
# doctor は「壊れている」と「古い」を別の診断として出せること、
# および hooks を数えられること。
#
# 守っている回帰（2026-09-10・2 度目の「merged ≠ shipped」）:
# main に merge しても `marketplace.json` の `source.ref` を上げなければ、
# 導入先には旧版が居座る。旧版はそれ自体としては健全なので、doctor は
# HEALTHY を返してしまう。実際に v2.5.0 のまま「skills 17 件・HEALTHY」と
# 出しながら、公開済みの hook 14 本 / engine 13 本 / skill 26 本が
# 1 つも入っていない状態を観測した。
#
# hooks を数えていなければ「配られていない」に気づく手段が無い。
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DOCTOR="$KIT_ROOT/scripts/check-kit-enabled.sh"
assert_file_exists "$DOCTOR" "doctor が在る"

# 偽の CLAUDE_CONFIG_DIR を組み立てる。<installed> と <marketplace> の版数を指定できる。
# $1=作業先 $2=installed ver $3=marketplace ver $4=hooks 本数 $5=hooks に +x を付けるか(yes/no)
_mkhome() {
  local home="$1" iver="$2" mver="$3" nhooks="$4" exec_ok="$5" ip
  ip="$home/plugins/cache/iwillink/willink-claude-kit/$iver"
  mkdir -p "$ip/.claude-plugin" "$ip/commands" "$ip/agents" "$ip/skills" \
           "$home/plugins/marketplaces/iwillink/.claude-plugin"
  printf '{"name":"willink-claude-kit","version":"%s"}\n' "$iver" > "$ip/.claude-plugin/plugin.json"
  : > "$ip/commands/build.md"; : > "$ip/agents/a.md"; mkdir -p "$ip/skills/s"
  if [ "$nhooks" -gt 0 ]; then
    mkdir -p "$ip/hooks"
    local i
    for i in $(seq 1 "$nhooks"); do
      printf '#!/usr/bin/env bash\nexit 0\n' > "$ip/hooks/h$i.sh"
      [ "$exec_ok" = "yes" ] && chmod +x "$ip/hooks/h$i.sh"
    done
  fi
  mkdir -p "$home/plugins"
  printf '{"plugins":{"willink-claude-kit@iwillink":[{"installPath":"%s","version":"%s"}]}}\n' \
    "$ip" "$iver" > "$home/plugins/installed_plugins.json"
  printf '{"plugins":[{"name":"willink-claude-kit","version":"%s"}]}\n' "$mver" \
    > "$home/plugins/marketplaces/iwillink/.claude-plugin/marketplace.json"
  # settings.json は [1] が見る。boolean true = 正常な有効化。
  printf '{"enabledPlugins":{"willink-claude-kit@iwillink":true}}\n' > "$home/settings.json"
}

# 出力はファイルへ落とす（assert_contains はファイルを取る。
# 文字列を渡すと grep がファイル不在で失敗し、**偽の PASS** になる）
_run() { CLAUDE_CONFIG_DIR="$1" bash "$DOCTOR" > "$OUT" 2>&1; }

TMP="$(mktemp -d)"
OUT="$TMP/out.txt"
trap 'rm -rf "$TMP"' EXIT

# --- pass 側: 版数一致 + hooks 14 本すべて実行可能 ---
_mkhome "$TMP/ok" 2.6.0 2.6.0 14 yes
_run "$TMP/ok"; rc=$?
assert_eq "0" "$rc" "版数一致 + hooks 揃いなら exit 0"
assert_contains "$OUT" "hooks/ … 14 本" "hooks を本数で数える"
assert_contains "$OUT" "版数一致" "版数一致を明示する"

# --- block 側 1: 旧版が居座っている（壊れてはいないが古い） ---
_mkhome "$TMP/old" 2.5.0 2.6.0 0 yes
_run "$TMP/old"; rc=$?
assert_eq "1" "$rc" "installed < marketplace なら exit 1"
assert_contains "$OUT" "旧版がロードされている" "「古い」を専用の診断として出す"
assert_not_contains "$OUT" "HEALTHY" "古い版を HEALTHY と言わない"

# --- block 側 2: hooks が同梱されていない ---
assert_contains "$OUT" "hooks/ が無い" "hooks 不在を検出する"

# --- 代替手順側: 案内どおり chmod +x すれば通る ---
_mkhome "$TMP/noexec" 2.6.0 2.6.0 3 no
_run "$TMP/noexec"; rc=$?
assert_eq "1" "$rc" "+x の無い hook があれば exit 1"
assert_contains "$OUT" "chmod +x" "案内する代替手順を出す"
chmod +x "$TMP/noexec"/plugins/cache/iwillink/willink-claude-kit/2.6.0/hooks/*.sh
_run "$TMP/noexec"; rc=$?
assert_eq "0" "$rc" "案内どおり chmod +x すれば通る"

# --- 不明を OK にも NG にもしない ---
_mkhome "$TMP/nomp" 2.6.0 2.6.0 2 yes
rm -f "$TMP/nomp/plugins/marketplaces/iwillink/.claude-plugin/marketplace.json"
_run "$TMP/nomp"; rc=$?
assert_contains "$OUT" "版数を比較できない" "marketplace が読めなければ不明と書く"
assert_eq "0" "$rc" "不明は単独では落とさない（0 件と書かないが NG にもしない）"

t_summary
