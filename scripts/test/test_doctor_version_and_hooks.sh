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
    local i reg=""
    for i in $(seq 1 "$nhooks"); do
      printf '#!/usr/bin/env bash\nexit 0\n' > "$ip/hooks/h$i.sh"
      [ "$exec_ok" = "yes" ] && chmod +x "$ip/hooks/h$i.sh"
      reg="$reg${reg:+,}{\"hooks\":[{\"type\":\"command\",\"command\":\"bash \\\"\${CLAUDE_PLUGIN_ROOT}/hooks/h$i.sh\\\"\"}]}"
    done
    # 2.7.0 以降: hook は hooks.json で登録される（置いてあるだけでは 1 本も登録されない）
    printf '{"hooks":{"PreToolUse":[%s]}}\n' "$reg" > "$ip/hooks/hooks.json"
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

# --- 2.7.0 以降: hooks は hooks.json で登録される（古い「settings.json へ自動登録されない」表示を出さない） ---
_mkhome "$TMP/reg" 2.9.0 2.9.0 3 yes
_run "$TMP/reg"; rc=$?
assert_eq "0" "$rc" "hooks.json で 3 本登録・参照先が揃っていれば exit 0"
assert_contains "$OUT" "hooks/hooks.json … 3 本を登録" "hooks.json の登録本数を数える"
assert_not_contains "$OUT" "自動登録されない" "古い表示（hooks は settings.json へ自動登録されない）を出さない"

# block: hooks.json が無い → 置いてあるだけでは 1 本も登録されない
rm -f "$TMP/reg"/plugins/cache/iwillink/willink-claude-kit/2.9.0/hooks/hooks.json
_run "$TMP/reg"; rc=$?
assert_eq "1" "$rc" "hooks.json が無ければ exit 1"
assert_contains "$OUT" "hooks/hooks.json が無い" "hooks.json 不在を検出する"

# block: hooks.json が参照する hook が無い
_mkhome "$TMP/dangling" 2.9.0 2.9.0 2 yes
rm -f "$TMP/dangling"/plugins/cache/iwillink/willink-claude-kit/2.9.0/hooks/h2.sh
_run "$TMP/dangling"; rc=$?
assert_eq "1" "$rc" "hooks.json の参照先が無ければ exit 1"
assert_contains "$OUT" "1 本の参照先が無い" "参照先の欠落を本数で出す"

# --- 記録の引き方: 先頭の記録（別リポの古い project scope）ではなく、このリポ → user の順 ---
_mkhome "$TMP/multi" 2.9.0 2.9.0 2 yes
ip="$TMP/multi/plugins/cache/iwillink/willink-claude-kit/2.9.0"
printf '{"plugins":{"willink-claude-kit@iwillink":[{"scope":"project","projectPath":"/some/other/repo","installPath":"/gone/2.5.0","version":"2.5.0"},{"scope":"user","installPath":"%s","version":"2.9.0"}]}}\n' \
  "$ip" > "$TMP/multi/plugins/installed_plugins.json"
( cd "$TMP" && CLAUDE_CONFIG_DIR="$TMP/multi" bash "$DOCTOR" > "$OUT" 2>&1 ); rc=$?
assert_eq "0" "$rc" "別リポの古い project の記録を拾わず、user の 2.9.0 で判定する"
assert_contains "$OUT" "scope=user / version=2.9.0" "どの scope の記録で判定したかを出す"

# --- 未インストール: まっさらな機では marketplace add を先に打つと案内する ---
mkdir -p "$TMP/empty/plugins"; printf '{"plugins":{}}\n' > "$TMP/empty/plugins/installed_plugins.json"
printf '{"enabledPlugins":{"willink-claude-kit@iwillink":true}}\n' > "$TMP/empty/settings.json"
_run "$TMP/empty"; rc=$?
assert_eq "1" "$rc" "未インストールなら exit 1"
assert_contains "$OUT" "claude plugin marketplace add willink-oss/willink-claude-kit" "直し方に marketplace add を出す"
assert_contains "$OUT" "claude plugin install willink-claude-kit@iwillink --scope project" "直し方に install を出す"

# --- 止める hook の実挙動: .env への Write を止め、普通のファイルは通す ---
_mkhome "$TMP/probe" 2.9.0 2.9.0 1 yes
hp="$TMP/probe/plugins/cache/iwillink/willink-claude-kit/2.9.0/hooks"
cp "$KIT_ROOT/hooks/pre-file-protect.sh" "$hp/pre-file-protect.sh"; chmod +x "$hp/pre-file-protect.sh"
_run "$TMP/probe"; rc=$?
assert_eq "0" "$rc" "本物の pre-file-protect.sh なら exit 0"
assert_contains "$OUT" ".env への Write を止め（exit 2）" "止める hook の実挙動を確かめる"
# 止めない hook（何もしない版）に差し替えると NG
printf '#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n' > "$hp/pre-file-protect.sh"
_run "$TMP/probe"; rc=$?
assert_eq "1" "$rc" "止めない pre-file-protect.sh なら exit 1"
assert_contains "$OUT" "期待どおりに動かない" "止めないことを NG として出す"
# hook が無い（古い版）→ 実挙動は測れていない（??）で、それ単独では落とさない
rm -f "$hp/pre-file-protect.sh"
_run "$TMP/probe"; rc=$?
assert_contains "$OUT" "実挙動は測れていない" "測れないときは不明と書く（0 件と書かない）"

t_summary
