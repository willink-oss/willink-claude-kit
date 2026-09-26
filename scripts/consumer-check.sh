#!/usr/bin/env bash
# =============================================================================
# consumer-check.sh — consumer で「ハーネスが効いている」を実測する汎用の検査器（plugin 経路）
#
# なぜ在るか（2026-09-17・責任者「更新のたびに consumer 側で対応が要るのは困る」）:
#   これまで consumer は install.sh で skill / engine / hook / fixture を **リポの中にコピー**し、
#   `scripts/harness-check.sh` を consumer ごとに手で書いていた（3 本目で 3 版目）。正本を直すたびに
#   再インストールと PR が要る。plugin 経路なら hook / skill / engine は kit（`willink-claude-kit`）の
#   cache から届き、fixture は CI が正本を checkout して当てる。consumer に残るのは
#   settings の数行・`.githooks` の resolver・薄い wrapper・CI の 1 段だけ。**この検査器も consumer には
#   置かない**（kit の cache か HARNESS_ROOT から呼ぶ）。
#
# 何を見るか（すべて分母つき・自己申告なし）:
#   1. コピー方式が残っていない（`.claude/willink-kit/` 無し・settings.json に willink-kit/hooks の inline 無し）
#   2. kit が settings.json で有効（project か user）。cache があれば hooks.json の登録本数と実体、
#      止める 2 本の block / pass を cache のスクリプトで probe（CI には cache が無いので ❓ と言う）
#   3. 配線経路が 1 本か（hook-wiring-check.py・plugin と inline の二重登録）
#   4. fixture: HARNESS_ROOT があれば self-test を全部回し、`.claude/harness-targets.json` の対象へ当てる。
#      HARNESS_ROOT が無く対象が宣言されていれば **exit 2（測れていない）**。宣言が無ければ自動検出して ℹ️
#   5. git hook（.githooks / .husky）: 実在・実行可。kit の pre-commit 3 本を temp repo で block / pass
#   6. 課題台帳（finding.py check・未同期の件数）
#
# 使い方（consumer の wrapper から）:
#   consumer-check.sh --root <consumer> [--kit <kit の cache>] [--harness <HARNESS_ROOT>] [--json]
#   consumer-check.sh --self-test
# Exit: 0 = 全項目 OK / 1 = NG あり / 2 = 測れていない項目がある（対象を宣言しているのに当てられない等）
# =============================================================================
set -uo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_PLUGIN="willink-claude-kit@iwillink"
TARGETS_REL=".claude/harness-targets.json"
ROOT=""; KIT=""; HARNESS="${HARNESS_ROOT:-}"; JSON=0
ng=0; unk=0; okn=0
ok()  { okn=$((okn+1)); printf '  ✅ %s\n' "$1"; }
bad() { ng=$((ng+1));   printf '  ❌ %s\n' "$1"; }
unm() { unk=$((unk+1)); printf '  ❓ %s\n' "$1"; }
info(){ printf '  ℹ️  %s\n' "$1"; }

# kit の cache を installed_plugins.json から引く（project scope でこの repo → user scope）
resolve_kit() {
  python3 - "$1" <<'PY'
import json, os, sys
root = os.path.realpath(sys.argv[1])
cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
try:
    es = json.load(open(os.path.join(cfg, "plugins", "installed_plugins.json"), encoding="utf-8")).get("plugins", {}).get("willink-claude-kit@iwillink", [])
except Exception:
    es = []
e = next((e for e in es if e.get("scope") == "project" and e.get("projectPath") and os.path.realpath(e["projectPath"]) == root), None) \
    or next((e for e in es if e.get("scope") == "user"), None)
print(e.get("installPath", "") if e else "")
PY
}

# 「engine を探す」: HARNESS_ROOT（正本・最新）→ kit の cache の順。kit は release 時点の snapshot なので、
# 正本の clone がある開発機ではそちらが新しい（CI は HARNESS_ROOT しか無い）
engine() {  # $1 = ファイル名
  for c in "$HARNESS/scripts/$1" "$KIT/scripts/$1"; do [ -n "$c" ] && [ -f "$c" ] && { printf '%s' "$c"; return 0; }; done
  return 1
}

run_checks() {
  cd "$ROOT" || { echo "❗ --root が無い: $ROOT" >&2; exit 2; }
  export CLAUDE_PROJECT_DIR="$ROOT"
  echo "── consumer-check（plugin 経路）: $ROOT"
  echo "   kit cache: ${KIT:-（無し）} / HARNESS_ROOT: ${HARNESS:-（無し）}"

  echo "── 1. コピー方式が残っていない"
  if [ -d .claude/willink-kit ]; then bad ".claude/willink-kit/ が残っている（コピー方式）— 消して plugin 経路へ"; else ok ".claude/willink-kit/ 無し"; fi
  n_inline=$(python3 -c '
import json,sys
try: d=json.load(open(".claude/settings.json"))
except Exception: print(-1); sys.exit()
print(sum(1 for ev in (d.get("hooks") or {}).values() for e in ev for h in e.get("hooks",[]) if "willink-kit/hooks/" in h.get("command","")))')
  if [ "$n_inline" = "-1" ]; then unm ".claude/settings.json が無い / 読めない（測れていない）"
  elif [ "$n_inline" != "0" ]; then bad "settings.json に willink-kit/hooks の inline 登録が ${n_inline} 本（kit の hooks.json と二重になる）— 外す"
  else ok "settings.json に willink-kit/hooks の inline 登録 0 本"; fi

  echo "── 2. kit（plugin）"
  kit_enabled=$(python3 -c '
import json,os
cfg=os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
def en(p):
    try: return json.load(open(p)).get("enabledPlugins",{}).get("willink-claude-kit@iwillink")
    except Exception: return None
v=None; scope="なし"
for s,p in (("user",os.path.join(cfg,"settings.json")),("project",".claude/settings.json"),("local",".claude/settings.local.json")):
    x=en(p)
    if x is not None: v,scope=x,s
print(f"{v} {scope}")')
  case "$kit_enabled" in
    "True project"|"True local") ok "kit を settings.json で有効化（${kit_enabled#True }）" ;;
    "True user") info "kit は user scope でだけ有効（settings.json に書いておくと clone 先でも効く）"; ok "kit 有効（user）" ;;
    *) bad "kit（$KIT_PLUGIN）が有効でない: $kit_enabled — settings.json の enabledPlugins に書く" ;;
  esac
  # hook の実体: kit の cache → 無ければ HARNESS_ROOT の core/hooks（kit は正本から export される同じスクリプト。
  # CI には cache が無いので、登録は settings.json で・挙動は正本のスクリプトで測る）
  HSRC=""; HSRC_LABEL=""
  if [ -n "$KIT" ] && [ -f "$KIT/hooks/hooks.json" ]; then HSRC="$KIT/hooks"; HSRC_LABEL="cache"
  elif [ -z "$KIT" ] && [ -n "$HARNESS" ] && [ -f "$HARNESS/core/hooks/hooks.json" ]; then HSRC="$HARNESS/core/hooks"; HSRC_LABEL="正本（cache 無し・CI）"; info "kit の cache が無い（CI）— 登録は settings.json、挙動は正本の core/hooks で測る"; fi
  if [ -n "$HSRC" ]; then
    n_reg=$(python3 -c '
import json,re,sys
d=json.load(open(sys.argv[1]))
print(sum(1 for ev in d["hooks"].values() for e in ev for h in e["hooks"]))' "$HSRC/hooks.json")
    miss=0; for f in $(python3 -c '
import json,re,sys
d=json.load(open(sys.argv[1]))
for ev in d["hooks"].values():
    for e in ev:
        for h in e["hooks"]:
            m=re.search(r"/hooks/([A-Za-z0-9._-]+\.sh)", h["command"]); print(m.group(1) if m else "?")' "$HSRC/hooks.json"); do [ -x "$HSRC/$f" ] || miss=$((miss+1)); done
    [ "$miss" -eq 0 ] && ok "hooks.json が ${n_reg} 本を登録・実体すべて実行可（$HSRC_LABEL）" || bad "hooks.json ${n_reg} 本のうち実体が無い / 実行不可 ${miss}（$HSRC_LABEL）"
    probe() { printf '%s' "$2" | bash "$HSRC/$1" >/dev/null 2>&1; echo $?; }
    rc=$(probe pre-bash-safety.sh '{"tool_name":"Bash","tool_input":{"command":"git push origin main"}}')
    [ "$rc" = "2" ] && ok "pre-bash-safety  block: 直 push → exit 2（$HSRC_LABEL）" || bad "pre-bash-safety  block 側: 直 push を止めない (exit $rc)"
    rc=$(probe pre-bash-safety.sh '{"tool_name":"Bash","tool_input":{"command":"git status"}}')
    [ "$rc" = "0" ] && ok "pre-bash-safety  pass : git status → exit 0" || bad "pre-bash-safety  pass 側: 無害なコマンドを止めた (exit $rc)"
    rc=$(probe pre-file-protect.sh '{"tool_name":"Edit","tool_input":{"file_path":"'"$ROOT"'/.env"}}')
    [ "$rc" = "2" ] && ok "pre-file-protect block: .env → exit 2" || bad "pre-file-protect block 側: .env を止めない (exit $rc)"
    rc=$(probe pre-file-protect.sh '{"tool_name":"Edit","tool_input":{"file_path":"'"$ROOT"'/.env.example"}}')
    [ "$rc" = "0" ] && ok "pre-file-protect pass : .env.example → exit 0" || bad "pre-file-protect pass 側: .env.example を止めた (exit $rc)"
  elif [ -n "$KIT" ]; then
    bad "kit の cache（$KIT）に hooks/hooks.json が無い — この版は hook を登録しない。\`claude plugin update $KIT_PLUGIN\`（2.7.0 以降）"
  else
    unm "kit の cache も HARNESS_ROOT も無い — hook の実効は測れていない（開発機: \`make setup\`（= bash scripts/setup.sh・marketplace add → install）／CI: HARNESS_ROOT）"
  fi

  echo "── 3. 配線経路は 1 本か"
  if hw=$(engine hook-wiring-check.py); then
    if [ -n "$KIT" ]; then out=$(python3 "$hw" --target "$ROOT" 2>&1); rc=$?
    elif [ -n "$HARNESS" ] && [ -f "$HARNESS/core/hooks/hooks.json" ]; then out=$(python3 "$hw" --target "$ROOT" --kit-hooks "$HARNESS/core/hooks/hooks.json" 2>&1); rc=$?
    else out=$(python3 "$hw" --target "$ROOT" 2>&1); rc=$?; fi
    line=$(printf '%s\n' "$out" | grep -E '^(✅|⚠️|❌|  ❓)' | head -1 | sed 's/^ *//')
    case "$rc" in 0) ok "hook-wiring-check: $line" ;; 1) bad "hook-wiring-check: $line" ;; *) unm "hook-wiring-check が測れていない: $line" ;; esac
  else
    unm "hook-wiring-check.py が kit の cache にも HARNESS_ROOT にも無い"
  fi

  echo "── 4. fixture（正本の checkout から当てる）"
  targets=$(python3 -c '
import json,os
p=".claude/harness-targets.json"
if not os.path.isfile(p): print("NONE"); raise SystemExit
try: d=json.load(open(p))
except Exception as e: print("BROKEN "+str(e)); raise SystemExit
for name,paths in (d.get("fixtures") or {}).items():
    for t in (paths if isinstance(paths,list) else [paths]): print(f"{name}\t{t}")')
  if [ "$targets" = "NONE" ]; then
    info "$TARGETS_REL が無い — 対象を宣言すると CI で当たる（自動検出だけ）"
    targets=""
    [ -f firestore.rules ] && targets="firestore-rules-access	firestore.rules"
    for d in lib apps/*/lib; do [ -d "$d" ] && targets="${targets}${targets:+
}not-wired-values	$d"; done
    [ -n "$targets" ] && info "自動検出: $(printf '%s' "$targets" | tr '\t' ':' | tr '\n' ' ')"
  elif case "$targets" in BROKEN*) true;; *) false;; esac; then
    bad "$TARGETS_REL が読めない: ${targets#BROKEN }"; targets=""
  fi
  if [ -n "$HARNESS" ] && [ -d "$HARNESS/fixtures" ]; then
    n_fix=0; for f in "$HARNESS"/fixtures/*/check.py; do
      [ -f "$f" ] || continue; n_fix=$((n_fix+1)); name=$(basename "$(dirname "$f")")
      if out=$(python3 "$f" --self-test 2>&1); then ok "$name self-test: $(printf '%s' "$out" | tail -1 | sed 's/^✅ //' | cut -c1-60)"; else bad "$name self-test: $(printf '%s' "$out" | tail -1 | cut -c1-80)"; fi
    done
    [ "$n_fix" -gt 0 ] || bad "HARNESS_ROOT に fixture が 0 件（分母ゼロ）"
    if [ -n "$targets" ]; then
      printf '%s\n' "$targets" | while IFS=$'\t' read -r name t; do
        [ -n "$name" ] || continue
        f="$HARNESS/fixtures/$name/check.py"
        if [ ! -f "$f" ]; then printf '  ❌ %s\n' "宣言した fixture が正本に無い: $name"; continue; fi
        if [ ! -e "$t" ]; then printf '  ❌ %s\n' "$name の対象が無い: $t（宣言と実体のずれ）"; continue; fi
        if out=$(python3 "$f" "$t" 2>&1); then printf '  ✅ %s\n' "$name → $t: 違反 0（$(printf '%s' "$out" | grep -oE '走査 [0-9]+ ?[^ /]*' | head -1)）"
        else rc=$?; printf '  ❌ %s\n' "$name → $t: exit $rc — $(printf '%s' "$out" | grep -E '^❌|^❗' | head -1 | cut -c1-90)"; fi
      done > "$ROOT/.consumer-check.fixtures.tmp"
      cat "$ROOT/.consumer-check.fixtures.tmp"
      ng=$((ng + $(grep -c '❌' "$ROOT/.consumer-check.fixtures.tmp"))); okn=$((okn + $(grep -c '✅' "$ROOT/.consumer-check.fixtures.tmp")))
      rm -f "$ROOT/.consumer-check.fixtures.tmp"
    else
      info "当てる対象が無い（宣言なし・自動検出なし）"
    fi
  else
    if [ -n "$targets" ]; then unm "fixture の対象を宣言しているが HARNESS_ROOT（正本の checkout）が無い — CI は PAT で checkout、開発機は HARNESS_ROOT=<clone>"
    else info "HARNESS_ROOT 無し・対象も無し（fixture は測っていない）"; fi
  fi

  echo "── 5. git hook"
  gdir=""; for d in .githooks .husky; do [ -d "$d" ] && gdir="$d"; done
  if [ -z "$gdir" ]; then info "git hook のディレクトリ（.githooks / .husky）が無い"
  else
    [ -x "$gdir/pre-commit" ] && ok "$gdir/pre-commit 実在・実行可" || bad "$gdir/pre-commit が無い / 実行不可"
    if [ "$gdir" = ".husky" ]; then
      if [ -x .husky/harness-pre-commit.sh ] && grep -q 'harness-pre-commit.sh' .husky/pre-commit 2>/dev/null; then ok ".husky/pre-commit が harness-pre-commit.sh（kit の 3 本の resolver）を呼ぶ"
      else info ".husky/pre-commit は kit の pre-commit 3 本を呼んでいない（wire-consumer.sh の案内どおり 1 行足すと当たる）"; fi
    fi
    hp=$(git config core.hooksPath 2>/dev/null || true); info "core.hooksPath='${hp:-未設定}'（clone ごとの設定・値を印字するだけ）"
  fi
  src=""; for c in "$KIT/hooks" "$HARNESS/core/hooks"; do [ -n "$c" ] && [ -x "$c/pre-commit-quality.sh" ] && { src="$c"; break; }; done
  if [ -n "$src" ]; then
    T=$(mktemp -d); ( cd "$T" && git init -q && git config user.email t@t && git config user.name t && git config commit.gpgsign false )
    ( cd "$T" && printf 'secret = "hunter2hunter2hunter2"\n' > bad.toml && git add bad.toml )
    ( cd "$T" && bash "$src/pre-commit-quality.sh" >/dev/null 2>&1 ); rc=$?
    [ "$rc" = "1" ] && ok "pre-commit-quality block: 直書きの secret → exit 1（$(basename "$(dirname "$src")")）" || bad "pre-commit-quality block 側: 直書きの secret を通した (exit $rc)"
    ( cd "$T" && git rm -q --cached bad.toml && rm -f bad.toml && printf 'x = "env(X)"\n' > ok.toml && git add ok.toml )
    ( cd "$T" && bash "$src/pre-commit-quality.sh" >/dev/null 2>&1 ); rc=$?
    [ "$rc" = "0" ] && ok "pre-commit-quality pass : env() 参照 → exit 0" || bad "pre-commit-quality pass 側: env() 参照を止めた (exit $rc)"
    rm -rf "$T"
  else
    unm "kit の pre-commit 3 本が cache にも HARNESS_ROOT にも無い（git hook の実効は測れていない）"
  fi

  echo "── 6. 課題台帳"
  if fp=$(engine finding.py); then
    if out=$(python3 "$fp" check --check --allow-missing 2>&1); then ok "finding.py check: $(printf '%s' "$out" | grep -E '走査|0 行|未作成' | head -1 | sed 's/^ *//')"
    else bad "finding.py check: $(printf '%s' "$out" | grep -E '❌' | head -1)"; fi
    n_unsynced=$(python3 "$fp" stats --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("unsynced",0))' 2>/dev/null || echo "?")
    [ "$n_unsynced" = "0" ] || printf '  ⚠️  未同期の課題 %s 件 — python3 %s sync --push（clone 不要）／正本の判定は pull --apply\n' "$n_unsynced" "$fp"
  else
    unm "finding.py が kit の cache にも HARNESS_ROOT にも無い"
  fi

  echo "───────────────────────────────"
  printf 'consumer-check: ✅ %d / ❌ %d / ❓ %d\n' "$okn" "$ng" "$unk"
  [ "$JSON" -eq 1 ] && printf '{"ok":%d,"ng":%d,"unmeasured":%d}\n' "$okn" "$ng" "$unk"
  [ "$ng" -gt 0 ] && exit 1
  [ "$unk" -gt 0 ] && exit 2
  exit 0
}

# ---------------------------------------------------------------------------
# self-test（hermetic）: 偽の kit cache（正本の core/hooks を写す）・偽の consumer・HARNESS_ROOT = 正本
# ---------------------------------------------------------------------------
self_test() {
  T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  PASS=0; FAIL=0
  t() { if [ "$2" = "$3" ]; then PASS=$((PASS+1)); printf '  ✅ %s\n' "$1"; else FAIL=$((FAIL+1)); printf '  ❌ %s（期待 %s / 実際 %s）\n' "$1" "$3" "$2"; fi; }
  printf '── consumer-check self-test（hermetic）──\n'
  # layout に依存しない: hook は正本（../../core/hooks）か kit（../hooks）、engine は自分のディレクトリ、
  # fixture は正本（../../fixtures）があればそれ、無ければ（kit の layout・① OSS は fixture を持たない）合成する。
  HOOKS_SRC=""; for c in "$SELF/../../core/hooks" "$SELF/../hooks"; do [ -f "$c/hooks.json" ] && { HOOKS_SRC="$(cd "$c" && pwd)"; break; }; done
  [ -n "$HOOKS_SRC" ] || { echo "  ❌ hooks.json が見つからない（正本 core/hooks・kit hooks のどちらにも無い）"; exit 1; }
  H="$T/harness"; mkdir -p "$H/core/hooks" "$H/scripts/engines" "$H/fixtures"
  cp "$HOOKS_SRC"/* "$H/core/hooks/"; cp "$SELF"/*.py "$H/scripts/" 2>/dev/null; cp "$SELF"/*.sh "$H/scripts/" 2>/dev/null
  if [ -d "$SELF/../../fixtures/not-wired-values" ]; then cp -R "$SELF/../../fixtures/." "$H/fixtures/"
  else
    mkdir -p "$H/fixtures/not-wired-values"
    cat > "$H/fixtures/not-wired-values/check.py" <<'PYF'
#!/usr/bin/env python3
"""合成 fixture（kit の layout の self-test 用）: 引数の dir の .dart に https:// があれば違反。--self-test は 1/1。"""
import sys, os
if "--self-test" in sys.argv: print("✅ self-test: 1/1 passed"); sys.exit(0)
d = sys.argv[1]; n = 0; bad = 0
for root, _, files in os.walk(d):
    for f in files:
        if f.endswith(".dart"):
            n += 1; bad += "https://" in open(os.path.join(root, f), encoding="utf-8").read()
print(f"走査 {n} 件 / 違反 {bad}"); sys.exit(1 if bad else (2 if n == 0 else 0))
PYF
  fi
  # 偽 kit cache
  K="$T/kit"; mkdir -p "$K/hooks" "$K/scripts"; cp "$H"/core/hooks/* "$K/hooks/"; cp "$H"/scripts/*.py "$K/scripts/"; chmod +x "$K"/hooks/*.sh
  # 偽 consumer（plugin 経路・良い形）
  C="$T/good"; mkdir -p "$C/.claude" "$C/.githooks" "$C/lib"
  ( cd "$C" && git init -q )
  printf '{"enabledPlugins":{"willink-claude-kit@iwillink":true}}\n' > "$C/.claude/settings.json"
  printf '#!/bin/sh\nexit 0\n' > "$C/.githooks/pre-commit"; chmod +x "$C/.githooks/pre-commit"
  printf "final base = config.apiBase;\n" > "$C/lib/a.dart"
  printf '{"schema_version":1,"fixtures":{"not-wired-values":["lib"]}}\n' > "$C/.claude/harness-targets.json"
  # 偽の installed_plugins.json（CLAUDE_CONFIG_DIR を temp に向ける）
  CFG="$T/cfg"; mkdir -p "$CFG/plugins"
  printf '{"version":2,"plugins":{"willink-claude-kit@iwillink":[{"scope":"project","projectPath":"%s","installPath":"%s","version":"9.9.9"}]}}\n' "$C" "$K" > "$CFG/plugins/installed_plugins.json"
  run() { ( CLAUDE_CONFIG_DIR="$CFG" bash "${BASH_SOURCE[0]}" "$@" >"$T/out.log" 2>&1 ); echo $?; }

  # pass 側: kit cache あり + HARNESS_ROOT あり + 対象あり → exit 0
  t "良い consumer（cache + HARNESS_ROOT + 対象）→ exit 0" "$(run --root "$C" --kit "$K" --harness "$H")" "0"
  grep -q 'not-wired-values → lib: 違反 0' "$T/out.log" && t "fixture を宣言どおり当てて分母を印字" 1 1 || t "fixture を宣言どおり当てて分母を印字" 0 1
  grep -q 'pre-bash-safety  block' "$T/out.log" && t "cache の hook を probe（block）" 1 1 || t "cache の hook を probe（block）" 0 1
  grep -q 'hook-wiring-check: ✅' "$T/out.log" && t "配線経路 1 本を ✅" 1 1 || t "配線経路 1 本を ✅" 0 1
  # kit の cache を wrapper と同じ手順で解決できる（--kit 省略）
  t "--kit 省略でも installed_plugins.json から cache を引く → exit 0" "$(run --root "$C" --harness "$H")" "0"
  # block 側: コピー方式が残っている
  mkdir -p "$C/.claude/willink-kit"; t "willink-kit/ が残っていると exit 1" "$(run --root "$C" --kit "$K" --harness "$H")" "1"; rmdir "$C/.claude/willink-kit"
  printf '{"enabledPlugins":{"willink-claude-kit@iwillink":true},"hooks":{"PreToolUse":[{"hooks":[{"type":"command","command":"bash $CLAUDE_PROJECT_DIR/.claude/willink-kit/hooks/pre-bash-safety.sh"}]}]}}\n' > "$C/.claude/settings.json"
  t "inline 登録が残っていると exit 1" "$(run --root "$C" --kit "$K" --harness "$H")" "1"
  printf '{"enabledPlugins":{"willink-claude-kit@iwillink":true}}\n' > "$C/.claude/settings.json"
  # block 側: fixture の違反
  printf "final base = 'https://example.test/api';\n" > "$C/lib/a.dart"
  t "fixture が違反を検出すると exit 1" "$(run --root "$C" --kit "$K" --harness "$H")" "1"
  printf "final base = config.apiBase;\n" > "$C/lib/a.dart"
  # 測れていない側: 対象を宣言しているのに HARNESS_ROOT 無し → exit 2
  t "対象を宣言・HARNESS_ROOT 無し → exit 2（0 件と言わない）" "$(HARNESS_ROOT='' run --root "$C" --kit "$K")" "2"
  grep -q '❓ fixture の対象を宣言しているが' "$T/out.log" && t "理由を ❓ で印字" 1 1 || t "理由を ❓ で印字" 0 1
  # CI 相当: cache 無し・HARNESS_ROOT あり → hook は ❓、fixture と wiring は測れる → exit 2（❓ が残る）
  printf '{"version":2,"plugins":{}}\n' > "$CFG/plugins/installed_plugins.json"
  t "cache 無し（CI 相当）・HARNESS_ROOT あり → 正本の hooks で probe して exit 0" "$(run --root "$C" --harness "$H")" "0"
  grep -q 'pre-bash-safety  block: 直 push → exit 2（正本' "$T/out.log" && t "CI 相当は正本の core/hooks で block / pass を測る" 1 1 || t "CI 相当は正本の core/hooks で block / pass を測る" 0 1
  grep -q 'not-wired-values → lib: 違反 0' "$T/out.log" && t "CI 相当でも fixture は当たる" 1 1 || t "CI 相当でも fixture は当たる" 0 1
  # CI とまったく同じ形: cache 無し・HARNESS_ROOT も --root も**相対パス**（CI は `HARNESS_ROOT=.harness` で呼ぶ）。
  # probe が一時ディレクトリへ cd しても hook を見失わないこと（2026-09-26: 絶対パスの試験だけでは exit 127 を見逃した）
  ln -s "$H" "$T/.harness"
  SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
  rc=$( (cd "$T" && CLAUDE_CONFIG_DIR="$CFG" HARNESS_ROOT=.harness bash "$SELF" --root good >"$T/out.log" 2>&1); echo $?)
  t "CI と同じ相対パス（HARNESS_ROOT=.harness・--root good）→ exit 0" "$rc" "0"
  grep -q 'pre-commit-quality block: 直書きの secret → exit 1' "$T/out.log" && t "相対パスでも pre-commit の probe が hook を見つける（exit 127 にならない）" 1 1 \
    || t "相対パスでも pre-commit の probe が hook を見つける（exit 127 にならない）" 0 1
  rm -f "$T/.harness"
  t "cache も HARNESS_ROOT も無し → exit 2（測れていない）" "$(HARNESS_ROOT='' run --root "$C")" "2"
  grep -q 'hook-wiring-check' "$T/out.log" && t "CI 相当でも配線経路は HARNESS_ROOT の hooks.json で測る" 1 1 || t "CI 相当でも配線経路は HARNESS_ROOT の hooks.json で測る" 0 1
  printf '{"version":2,"plugins":{"willink-claude-kit@iwillink":[{"scope":"project","projectPath":"%s","installPath":"%s","version":"9.9.9"}]}}\n' "$C" "$K" > "$CFG/plugins/installed_plugins.json"
  # kit が無効
  printf '{"enabledPlugins":{"willink-claude-kit@iwillink":false}}\n' > "$C/.claude/settings.json"
  t "kit が無効なら exit 1" "$(run --root "$C" --kit "$K" --harness "$H")" "1"
  printf '{"enabledPlugins":{"willink-claude-kit@iwillink":true}}\n' > "$C/.claude/settings.json"
  # 宣言した対象が実在しない
  printf '{"schema_version":1,"fixtures":{"not-wired-values":["nope"]}}\n' > "$C/.claude/harness-targets.json"
  t "宣言した対象が無いと exit 1（宣言と実体のずれ）" "$(run --root "$C" --kit "$K" --harness "$H")" "1"
  printf '{"schema_version":1,"fixtures":{"not-wired-values":["lib"]}}\n' > "$C/.claude/harness-targets.json"
  # cache が hooks.json を持たない版（2.5.0 相当）
  K2="$T/kit-old"; mkdir -p "$K2/hooks" "$K2/scripts"; cp "$K"/scripts/*.py "$K2/scripts/"
  t "cache が hooks.json を持たない版なら exit 1（update を促す）" "$(run --root "$C" --kit "$K2" --harness "$H")" "1"
  grep -q 'claude plugin update' "$T/out.log" && t "update の手順を印字" 1 1 || t "update の手順を印字" 0 1

  printf '\n検査 %d 件: 合格 %d / 不合格 %d\n' "$((PASS+FAIL))" "$PASS" "$FAIL"
  [ "$FAIL" -eq 0 ] || exit 1
  printf '✅ PASS — consumer-check は plugin 経路の consumer を block / pass / 測れていない の 3 側で判定する\n'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="$2"; shift 2 ;;
    --kit) KIT="$2"; shift 2 ;;
    --harness) HARNESS="$2"; shift 2 ;;
    --json) JSON=1; shift ;;
    --self-test) self_test; exit $? ;;
    *) echo "usage: consumer-check.sh --root <consumer> [--kit <cache>] [--harness <HARNESS_ROOT>] [--json] | --self-test" >&2; exit 2 ;;
  esac
done
[ -n "$ROOT" ] || { echo "usage: consumer-check.sh --root <consumer> [--kit <cache>] [--harness <HARNESS_ROOT>]" >&2; exit 2; }
# 場所は最初に絶対パスへ直す。probe は一時ディレクトリへ cd してから hook を呼ぶので、CI の
# `HARNESS_ROOT=.harness`（相対）のままだと hook が見つからず exit 127 になる（2026-09-26 実測・consumer 4 本で）
abspath() { if [ -n "$1" ] && [ -d "$1" ]; then (cd "$1" && pwd); else printf '%s' "$1"; fi; }
ROOT="$(abspath "$ROOT")"; HARNESS="$(abspath "$HARNESS")"; KIT="$(abspath "$KIT")"
[ -n "$KIT" ] || KIT="$(resolve_kit "$ROOT")"
run_checks
