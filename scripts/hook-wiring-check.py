#!/usr/bin/env python3
"""hook-wiring-check.py — Core hook の配線経路が **1 本**かを実測する。

なぜ在るか（2026-09-17 実測）:

  Core の hook は 2 つの経路で consumer に届く。
    (1) plugin 経路   … OSS kit（`willink-claude-kit@iwillink`）が `hooks/hooks.json` で登録する
                        （2.7.0 以降・cache はリポの外）
    (2) install.sh 経路 … `.claude/willink-kit/hooks/*.sh` を置き、settings.json に **人手で** inline 登録
  Claude Code は両方を**そのまま両方**登録する。合成 plugin で 1 prompt を送ると UserPromptSubmit が
  plugin 側と inline 側で 1 回ずつ = 2 回走った（dedup は無い）。kit 2.7.0 の hooks.json 10 本と、
  install.sh 経路で inline 登録した 10 本は**同名で 10/10 重なる**。

  consumer が kit を有効にしたまま inline 配線すると（実案件の 1 本目で起きた）、**cache の kit が
  hooks.json を持つ版に更新された瞬間に 10 本が 2 回ずつ走る**（止める 3 本は 2 回止め、ログは 2 重、review-gate は
  毎 prompt 2 回）。更新は `/plugin marketplace update` 1 回で起きるので、配線した日には見えない。

何を見るか（すべて live・自己申告なし）:

  - settings は Claude Code と同じ 3 層を **user → project → local の順に重ねて**読む:
    `$CLAUDE_CONFIG_DIR/settings.json`（user・`claude plugin install` の既定 scope はここに書く）→
    `<target>/.claude/settings.json` → `settings.local.json`。`enabledPlugins` は後勝ち。
    user scope を読まないと、開発機で kit を有効にしている consumer が「未宣言 ✅」と出る（初版の穴）
  - inline: 3 層の `hooks` のうち `/hooks/<name>.sh` を呼ぶ command（イベント × スクリプト名）
  - plugin: kit が有効なら `plugins/installed_plugins.json` から **この target に効く install**
    （scope=project かつ projectPath 一致 → scope=user）を引き、その cache の `hooks/hooks.json` を読む
  - 二重 = 両方に在る（イベント, スクリプト名）。**同じスクリプトでもイベントが違えば二重ではない**。
    matcher は見ない（kit の hooks.json は matcher を持たず、inline も同じ形で写すので今は差が出ない。
    将来 matcher で分ける配線が出たら (event, matcher, script) に広げる）

判定:
  exit 0 … 二重 0 本（kit が有効で cache に hooks.json が無い = 更新で二重になる本数を ⚠️ で印字）
  exit 1 … 二重 1 本以上（❌ と、代替手順 (A)(B) を印字）
  exit 2 … 測れていない（settings.json が無い / settings・registry・hooks.json のどれかが読めない /
           想定外の例外）。**0 本ではない**

代替手順（ゲートが案内するもの・どちらか 1 本に）:
  (A) inline を settings.json から外し、plugin の hooks.json に任せる
      — in-repo の hook 修正は kit の次の版まで届かない。git の pre-commit hook 3 本は元から in-repo
  (B) `enabledPlugins` の kit を false にし、install.sh 経路だけにする
      — kit の skill（/build 等）も消えるので、skill だけ要るなら (A)

usage:
  hook-wiring-check.py [--target <repo>] [--plugin <name@marketplace>] [--kit-hooks <hooks.json>]
                       [--config-dir <dir>] [--json]
  hook-wiring-check.py --self-test
"""

import argparse
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _phroot import target_root  # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_PLUGIN = "willink-claude-kit@iwillink"
HOOK_RE = re.compile(r"/hooks/([A-Za-z0-9._-]+\.sh)")
# kit が hooks.json で登録する 13 本（2.7.0 の 10 本 + 2026-09-25 の oneshot 3 本）。**cache の hooks.json が読めるときはそちらが正**で、名前表は
# cache に hooks.json が無い（= まだ登録しない版）ときに「更新されたら二重になる本数」を見積もるためだけに使う。
# inline には kit と無関係の hook（他ツールの通知など）も並ぶので、名前で絞らないと潜在の本数が水増しになる。
# 名前の出所は **配布物の hooks.json を先に読む**（`kit_hook_names()`・install.sh が
# `.claude/willink-kit/hooks/hooks.json` を配る）。この固定表はそれも読めないときの最後の fallback で、
# self-test が正本 / 配布物の hooks.json と一致することを固定する（表だけ古くなると偽の ✅ になる）。
KIT_HOOK_NAMES = frozenset((
    "pre-bash-safety.sh", "pre-file-protect.sh", "pre-write-collision.sh", "post-commit-verify.sh",
    "post-file-eval.sh", "post-tool-log.sh", "pre-status-verify-guard.sh", "review-gate.sh",
    "pre-compact-snapshot.sh", "instructions-loaded-log.sh",
    "post-oneshot-elapsed.sh", "stop-oneshot-continue.sh",   # 2026-09-25 /oneshot の無人区間（state.json が無ければ何もしない）
    "pre-oneshot-scope.sh",                                  # 2026-09-25 同上・scope 外への Write と外への到達を止める
))


# ---------------------------------------------------------------------------
# 読む
# ---------------------------------------------------------------------------
def hooks_json_candidates(target):
    """kit の hooks.json が置かれ得る場所（読めた最初の 1 つを使う）。**このスクリプト自身の位置**から引く
    （3 つの layout で在処が違う）:
      consumer に配られた willink-kit/hooks/     … <target>/.claude/willink-kit/hooks/hooks.json
      正本（scripts/ にあるとき）        … ../../core/hooks/hooks.json
      OSS kit（scripts/ にあるとき）・配布物（engines/ にあるとき） … ../hooks/hooks.json
    2026-09-17: harness_home()（= 2 つ上）で組んだら kit の layout で kit の親を指し、CI の
    「配布物が公開先の形で自己証明するか」で self-test が落ちた。"""
    return [os.path.join(target, ".claude", "willink-kit", "hooks", "hooks.json"),
            os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "core", "hooks", "hooks.json")),
            os.path.normpath(os.path.join(SCRIPT_DIR, "..", "hooks", "hooks.json"))]


def kit_hook_names(target):
    """(kit の hook 名の集合, 出所) を返す。hooks.json が 1 つも読めなければ固定表。"""
    for p in hooks_json_candidates(target):
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    names = {n for _ev, n in hook_pairs(json.load(fh))}
                if names:
                    return frozenset(names), p
            except (OSError, ValueError, AttributeError):
                continue
    return KIT_HOOK_NAMES, "固定表（hooks.json が読めない）"


def hook_pairs(doc):
    """settings.json / hooks.json 共通の形 {"hooks": {event: [{matcher?, hooks:[{command}]}]}} から
    (event, script) の集合を返す。/hooks/<name>.sh を呼ばない command は数えない。"""
    out = set()
    for ev, entries in (doc.get("hooks") or {}).items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            for h in (e.get("hooks") or []) if isinstance(e, dict) else []:
                m = HOOK_RE.search(h.get("command", "") if isinstance(h, dict) else "")
                if m:
                    out.add((ev, m.group(1)))
    return out


def read_settings(target, config_dir):
    """(inline の集合, enabledPlugins の合成 dict, plugin ごとの決定 scope, 読めた project 側の
    ファイル数, 壊れたファイル名, inline の層別本数) を返す。
    user（config_dir/settings.json）→ project → local の順に重ね、後勝ち（Claude Code と同じ向き）。
    「読めた数」は project 側だけを数える: user の settings は無くても普通で、consumer を測れない
    ことにはならない。"""
    layers = [("user", os.path.join(config_dir, "settings.json")),
              ("project", os.path.join(target, ".claude", "settings.json")),
              ("local", os.path.join(target, ".claude", "settings.local.json"))]
    inline, enabled, scope_of, n_read, broken, per_layer, pair_scopes = set(), {}, {}, 0, [], {}, {}
    for scope, p in layers:
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            if not isinstance(d, dict):
                raise ValueError("top-level が object でない")
        except (OSError, ValueError):
            broken.append("%s:%s" % (scope, p))
            continue
        if scope != "user":
            n_read += 1
        pairs = hook_pairs(d)
        per_layer[scope] = len(pairs)
        inline |= pairs
        for pr in pairs:
            pair_scopes.setdefault(pr, []).append(scope)
        for k, v in (d.get("enabledPlugins") or {}).items():
            enabled[k] = v
            scope_of[k] = scope
    return inline, enabled, scope_of, n_read, broken, per_layer, pair_scopes


def find_kit_hooks(config_dir, plugin, target):
    """installed_plugins.json から **この target に効く** install を選び、
    (hooks.json のパス or None, version, 理由) を返す。"""
    reg = os.path.join(config_dir, "plugins", "installed_plugins.json")
    if not os.path.isfile(reg):
        return None, None, "installed_plugins.json が無い（%s）" % reg
    try:
        with open(reg, encoding="utf-8") as fh:
            entries = (json.load(fh).get("plugins") or {}).get(plugin) or []
    except (OSError, ValueError, AttributeError):
        return "unreadable", None, "installed_plugins.json が読めない（parse 不能）: %s" % reg
    if not isinstance(entries, list):
        entries = [entries]
    tgt = os.path.realpath(target)
    chosen = None
    for e in entries:  # project scope でこの target のもの（projectPath 無しは cwd に解決させない）
        if e.get("scope") == "project" and e.get("projectPath") \
                and os.path.realpath(e["projectPath"]) == tgt:
            chosen = e
            break
    if chosen is None:  # 次に user scope（全 project に効く）
        chosen = next((e for e in entries if e.get("scope") == "user"), None)
    if chosen is None:
        return None, None, "この target に効く install が無い（%d 件のうち 0）" % len(entries)
    hp = os.path.join(chosen.get("installPath", ""), "hooks", "hooks.json")
    ver = chosen.get("version")
    if not os.path.isfile(hp):
        return None, ver, "cache %s に hooks/hooks.json が無い（この版は hook を登録しない）" % (ver or "?")
    return hp, ver, "scope=%s" % chosen.get("scope")


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def analyze(target, plugin=DEFAULT_PLUGIN, kit_hooks=None, config_dir=None):
    """dict を返す。exit は呼び側が `r["exit"]` で決める。"""
    r = {"target": target, "plugin": plugin, "inline": [], "inline_by_scope": {}, "inline_scopes": {},
         "kit_hooks": None, "kit_version": None, "kit_enabled": None, "kit_scope": None, "plugin_hooks": [],
         "double": [], "latent": [], "latent_source": None, "exit": 0, "note": ""}
    cd = config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    inline, enabled, scope_of, n_read, broken, per_layer, pair_scopes = read_settings(target, cd)
    if broken:  # どの層でも読めないものがあれば測れていない（user の settings が壊れていても plugin の有効/無効が分からない）
        r["exit"] = 2
        r["note"] = "settings が読めない: %s" % ", ".join(broken)
        return r
    if n_read == 0:
        r["exit"] = 2
        r["note"] = ".claude/settings.json が無い（測れていない・0 本ではない）"
        return r
    r["inline"] = sorted(inline)
    r["inline_by_scope"] = per_layer
    r["inline_scopes"] = {"%s %s" % pr: sc for pr, sc in pair_scopes.items()}
    r["kit_enabled"] = enabled.get(plugin)  # True / False / None（未宣言）
    r["kit_scope"] = scope_of.get(plugin)
    if r["kit_enabled"] is not True:
        r["note"] = "plugin %s は %s → plugin 経路は無い" % (plugin, "無効" if r["kit_enabled"] is False else "未宣言")
        return r
    if kit_hooks is None:
        kit_hooks, r["kit_version"], why = find_kit_hooks(cd, plugin, target)
        r["note"] = why
        if kit_hooks == "unreadable":
            r["exit"] = 2
            r["kit_hooks"] = None
            return r
    r["kit_hooks"] = kit_hooks
    if kit_hooks is None:
        # plugin は有効だが、この cache は hook を登録しない版。更新で二重になる本数を latent として出す
        names, r["latent_source"] = kit_hook_names(target)
        r["latent"] = sorted(p for p in inline if p[1] in names)
        return r
    try:
        with open(kit_hooks, encoding="utf-8") as fh:
            plug = hook_pairs(json.load(fh))
    except (OSError, ValueError):
        r["exit"] = 2
        r["note"] = "plugin の hooks.json が parse 不能: %s" % kit_hooks
        return r
    r["plugin_hooks"] = sorted(plug)
    r["double"] = sorted(inline & plug)
    r["exit"] = 1 if r["double"] else 0
    return r


def render(r):
    lines = ["── hook-wiring-check（Core hook の配線経路は 1 本か）──", "  対象: %s" % r["target"]]
    if r["exit"] == 2:
        lines.append("  ❓ %s" % r["note"])
        return "\n".join(lines)
    by = r.get("inline_by_scope") or {}
    lines.append("  inline（user / project / local の settings・/hooks/*.sh を呼ぶもの）: %d 本（%s）"
                 % (len(r["inline"]), "・".join("%s %d" % (k, by[k]) for k in ("user", "project", "local") if k in by) or "層なし"))
    ke = r["kit_enabled"]
    state = ("有効（%s scope）" % r.get("kit_scope")) if ke is True else ("無効（%s scope）" % r.get("kit_scope") if ke is False else "未宣言（user / project / local のどこにも無い）")
    lines.append("  plugin %s: %s" % (r["plugin"], state))
    if ke is not True:
        lines.append("✅ 経路 1 本（inline %d 本・plugin 経路なし）" % len(r["inline"]))
        return "\n".join(lines)
    if r["kit_hooks"] is None:
        lines.append("  plugin の hooks.json: 無し — %s" % r["note"])
        if r["latent"]:
            lines.append("⚠️  今は二重 0 本。plugin が hooks.json を持つ版（kit 2.7.0 以降）に更新されると "
                         "inline のうち kit と同名の %d 本 / %d が二重になる。更新前に (A)(B) のどちらかへ"
                         % (len(r["latent"]), len(r["inline"])))
            lines.append("   （kit の hook 名の出所: %s）" % r["latent_source"])
        else:
            lines.append("✅ 経路 1 本（inline %d 本のうち kit と同名 0・plugin の hooks.json も無し）" % len(r["inline"]))
        return "\n".join(lines)
    lines.append("  plugin の hooks.json: %s（%s）: %d 本" % (r["kit_hooks"], r["kit_version"] or "版不明", len(r["plugin_hooks"])))
    lines.append("  二重登録: %d 本 / inline %d" % (len(r["double"]), len(r["inline"])))
    for ev, name in r["double"]:
        lines.append("    - %s %s（inline の層: %s）" % (ev, name, "・".join(r["inline_scopes"].get("%s %s" % (ev, name), ["?"]))))
    if r["double"]:
        lines.append("❌ 同じ hook が plugin と inline の両方で走る（止める hook は 2 回止め、ログは 2 重）。どちらか 1 本に:")
        lines.append("   (A) inline の %d 本を settings.json から外し plugin に任せる（in-repo の hook 修正は kit の次の版まで届かない）"
                     % len(r["double"]))
        lines.append("   (B) enabledPlugins の %s を false にして install.sh 経路だけにする（kit の skill も消える）" % r["plugin"])
    else:
        lines.append("✅ 経路 1 本（二重 0 本 / inline %d・plugin %d）" % (len(r["inline"]), len(r["plugin_hooks"])))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# self-test（hermetic: 設定ディレクトリも cache も temp に作る）
# ---------------------------------------------------------------------------
def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _settings(hooks, enabled):
    return {"enabledPlugins": enabled,
            "hooks": {ev: [{"hooks": [{"type": "command",
                                       "command": 'bash "$CLAUDE_PROJECT_DIR/.claude/willink-kit/hooks/%s"' % n}]}]
                      for ev, n in hooks}}


def _hooks_json(hooks):
    return {"hooks": {ev: [{"hooks": [{"type": "command",
                                       "command": 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/%s"' % n}]}]
                      for ev, n in hooks}}


def self_test():
    n_pass = n_fail = 0

    def chk(label, got, exp):
        nonlocal n_pass, n_fail
        if got == exp:
            n_pass += 1
            print("  ✅ %s" % label)
        else:
            n_fail += 1
            print("  ❌ %s（期待 %r / 実際 %r）" % (label, exp, got))

    print("── hook-wiring-check self-test（hermetic）──")
    two = [("PreToolUse", "pre-bash-safety.sh"), ("UserPromptSubmit", "review-gate.sh")]
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "cfg")
        cache_new = os.path.join(tmp, "cache", "2.7.0")   # hooks.json を持つ版
        cache_old = os.path.join(tmp, "cache", "2.5.0")   # 持たない版
        _write(os.path.join(cache_new, "hooks", "hooks.json"), _hooks_json(two))
        os.makedirs(os.path.join(cache_old, "hooks"), exist_ok=True)
        proj = os.path.join(tmp, "proj")
        other = os.path.join(tmp, "other")
        os.makedirs(proj); os.makedirs(other)

        def registry(entries):
            _write(os.path.join(cfg, "plugins", "installed_plugins.json"),
                   {"version": 2, "plugins": {DEFAULT_PLUGIN: entries}})

        # --- block 側: kit 有効 + cache に hooks.json + inline 同名 2 本 → 二重 2・exit 1 ----
        registry([{"scope": "project", "projectPath": proj, "installPath": cache_new, "version": "2.7.0"}])
        _write(os.path.join(proj, ".claude", "settings.json"), _settings(two, {DEFAULT_PLUGIN: True}))
        r = analyze(proj, config_dir=cfg)
        chk("block: kit 2.7.0（hooks.json 2 本）+ inline 同名 2 本 → exit 1", r["exit"], 1)
        chk("block: 二重は 2 本", len(r["double"]), 2)
        chk("block: 出力に ❌ と代替手順 (A)(B)", all(s in render(r) for s in ("❌", "(A)", "(B)")), True)

        # --- 代替 (A): inline を外す → exit 0 ------------------------------------------
        _write(os.path.join(proj, ".claude", "settings.json"), _settings([], {DEFAULT_PLUGIN: True}))
        r = analyze(proj, config_dir=cfg)
        chk("代替 (A): inline 0 本・plugin 2 本 → exit 0", (r["exit"], len(r["double"])), (0, 0))

        # --- 代替 (B): kit を false → plugin 経路なし → exit 0 ---------------------------
        _write(os.path.join(proj, ".claude", "settings.json"), _settings(two, {DEFAULT_PLUGIN: False}))
        r = analyze(proj, config_dir=cfg)
        chk("代替 (B): kit 無効・inline 2 本 → exit 0", (r["exit"], r["kit_enabled"]), (0, False))
        _write(os.path.join(proj, ".claude", "settings.json"), _settings(two, {}))
        r = analyze(proj, config_dir=cfg)
        chk("pass: kit 未宣言（どの層にも無い）・inline 2 本 → exit 0", (r["exit"], r["kit_enabled"]), (0, None))

        # --- latent: kit 有効 + cache が hooks.json を持たない版 + inline 2 本 → exit 0 + ⚠️ 2 本 --
        registry([{"scope": "project", "projectPath": proj, "installPath": cache_old, "version": "2.5.0"}])
        _write(os.path.join(proj, ".claude", "settings.json"), _settings(two, {DEFAULT_PLUGIN: True}))
        r = analyze(proj, config_dir=cfg)
        chk("latent: kit 2.5.0（hooks.json 無し）+ inline 2 本 → exit 0・⚠️ 2 本", (r["exit"], len(r["latent"])), (0, 2))
        chk("latent: 出力に ⚠️（黙って緑にしない）", "⚠️" in render(r), True)
        _write(os.path.join(proj, ".claude", "settings.json"),
               _settings(two + [("Stop", "notify.sh")], {DEFAULT_PLUGIN: True}))
        r = analyze(proj, config_dir=cfg)
        chk("latent: kit と無関係の hook（notify.sh）は潜在に数えない → inline 3 / 潜在 2", (len(r["inline"]), len(r["latent"])), (3, 2))
        _write(os.path.join(proj, ".claude", "settings.json"),
               _settings([("Stop", "notify.sh")], {DEFAULT_PLUGIN: True}))
        r = analyze(proj, config_dir=cfg)
        chk("latent: kit と同名が 0 なら ✅（inline 1 本は他ツール）", (r["exit"], len(r["latent"]), "✅" in render(r)), (0, 0, True))

        # --- 同じスクリプトでもイベントが違えば二重ではない ----------------------------
        registry([{"scope": "project", "projectPath": proj, "installPath": cache_new, "version": "2.7.0"}])
        _write(os.path.join(proj, ".claude", "settings.json"),
               _settings([("PostToolUse", "pre-bash-safety.sh")], {DEFAULT_PLUGIN: True}))
        r = analyze(proj, config_dir=cfg)
        chk("イベント違いの同名は二重に数えない → 0 本", len(r["double"]), 0)

        # --- settings.local.json の inline も数える（project の上に載る） ------------------
        _write(os.path.join(proj, ".claude", "settings.json"), _settings([], {DEFAULT_PLUGIN: True}))
        _write(os.path.join(proj, ".claude", "settings.local.json"), _settings(two[:1], {}))
        r = analyze(proj, config_dir=cfg)
        chk("settings.local.json の inline 1 本も二重に数える → exit 1", (r["exit"], len(r["double"])), (1, 1))
        os.remove(os.path.join(proj, ".claude", "settings.local.json"))

        # --- install の選び方: project scope（一致）> user scope ----------------------------
        registry([{"scope": "user", "installPath": cache_old, "version": "2.5.0"},
                  {"scope": "project", "projectPath": proj, "installPath": cache_new, "version": "2.7.0"}])
        _write(os.path.join(proj, ".claude", "settings.json"), _settings(two, {DEFAULT_PLUGIN: True}))
        r = analyze(proj, config_dir=cfg)
        chk("この target の project scope（2.7.0）を user scope（2.5.0）より優先 → exit 1", r["exit"], 1)
        registry([{"scope": "user", "installPath": cache_new, "version": "2.7.0"},
                  {"scope": "project", "projectPath": other, "installPath": cache_old, "version": "2.5.0"}])
        r = analyze(proj, config_dir=cfg)
        chk("他 project の install は無視し user scope（2.7.0）を採る → exit 1", r["exit"], 1)

        # --- 分母: settings.json が無い → exit 2（0 本と言わない） ---------------------------
        r = analyze(other, config_dir=cfg)
        chk("settings.json が無い → exit 2（❓）", (r["exit"], "❓" in render(r)), (2, True))
        os.makedirs(os.path.join(other, ".claude"), exist_ok=True)
        with open(os.path.join(other, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
            fh.write("{ broken")
        r = analyze(other, config_dir=cfg)
        chk("settings.json が parse 不能 → exit 2", r["exit"], 2)

        # --- user scope（config_dir/settings.json）の enabledPlugins を読む（初版の穴・レビュー指摘） ---
        _write(os.path.join(cfg, "settings.json"), {"enabledPlugins": {DEFAULT_PLUGIN: True}})
        registry([{"scope": "user", "installPath": cache_new, "version": "2.7.0"}])
        _write(os.path.join(proj, ".claude", "settings.json"), _settings(two, {}))
        r = analyze(proj, config_dir=cfg)
        chk("user scope で kit 有効・project は未宣言・inline 2 本・cache 2.7.0 → exit 1（偽の ✅ を出さない）",
            (r["exit"], r["kit_scope"], len(r["double"])), (1, "user", 2))
        chk("表示に『有効（user scope）』", "有効（user scope）" in render(r), True)
        _write(os.path.join(proj, ".claude", "settings.json"), _settings(two, {DEFAULT_PLUGIN: False}))
        r = analyze(proj, config_dir=cfg)
        chk("project の false が user の true に勝つ（後勝ち）→ exit 0", (r["exit"], r["kit_scope"]), (0, "project"))
        _write(os.path.join(cfg, "settings.json"),
               {"enabledPlugins": {DEFAULT_PLUGIN: True},
                "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "bash ~/.claude/hooks/pre-bash-safety.sh"}]}]}})
        _write(os.path.join(proj, ".claude", "settings.json"), _settings([], {}))
        r = analyze(proj, config_dir=cfg)
        chk("user scope の inline hook も数える（inline user 1）→ exit 1",
            (r["exit"], r["inline_by_scope"].get("user"), len(r["double"])), (1, 1, 1))
        os.remove(os.path.join(cfg, "settings.json"))

        # --- 読めないものは「無い」に畳まず exit 2 ------------------------------------
        _write(os.path.join(proj, ".claude", "settings.json"), _settings(two, {DEFAULT_PLUGIN: True}))
        with open(os.path.join(cfg, "plugins", "installed_plugins.json"), "w", encoding="utf-8") as fh:
            fh.write("{ broken registry")
        r = analyze(proj, config_dir=cfg)
        chk("installed_plugins.json が parse 不能 → exit 2（⚠️ 潜在 0 と言わない）", (r["exit"], "❓" in render(r)), (2, True))
        with open(os.path.join(proj, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
            fh.write("[]")
        r = analyze(proj, config_dir=cfg)
        chk("settings.json が valid JSON だが object でない（[]）→ exit 2", r["exit"], 2)
        registry([{"scope": "project", "installPath": cache_new, "version": "2.7.0"}])  # projectPath 無し
        _write(os.path.join(proj, ".claude", "settings.json"), _settings(two, {DEFAULT_PLUGIN: True}))
        r = analyze(proj, config_dir=cfg)
        chk("projectPath の無い project scope は cwd に解決せず『効く install 無し』→ exit 0・⚠️", (r["exit"], len(r["latent"])), (0, 2))

        # --- main(): 想定外の例外は traceback でなく ❓ exit 2（advisory 側が黙らない） ------------
        import io, contextlib
        g = globals()
        saved = g["analyze"]
        def boom(*_a, **_k):
            raise RuntimeError("synthetic")
        g["analyze"] = boom
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = main(["--target", proj, "--config-dir", cfg])
        finally:
            g["analyze"] = saved
        chk("main: 想定外の例外 → ❓ を印字して exit 2", (rc, "❓" in buf.getvalue()), (2, True))

        # --- 固定表の drift guard: 正本 / 配布物の hooks.json と一致する（表だけ古くなると偽の ✅） ------
        src = next((p for p in hooks_json_candidates(proj) if os.path.isfile(p)), None)
        if src is None:
            chk("固定表 KIT_HOOK_NAMES を突き合わせる hooks.json が無い（正本 core/hooks・kit hooks・配布物のどれも）", False, True)
        else:
            with open(src, encoding="utf-8") as fh:
                real = {n for _ev, n in hook_pairs(json.load(fh))}
            chk("固定表 KIT_HOOK_NAMES == %s の hook 名（%d 本）" % (os.path.relpath(src, SCRIPT_DIR), len(real)),
                set(KIT_HOOK_NAMES), real)
        # 配布物の hooks.json（willink-kit/hooks/）があればそれを固定表より優先する
        _write(os.path.join(proj, ".claude", "willink-kit", "hooks", "hooks.json"),
               _hooks_json([("SessionStart", "renamed-in-next-kit.sh")]))
        registry([{"scope": "project", "projectPath": proj, "installPath": cache_old, "version": "2.5.0"}])
        _write(os.path.join(proj, ".claude", "settings.json"),
               _settings([("SessionStart", "renamed-in-next-kit.sh")] + two, {DEFAULT_PLUGIN: True}))
        r = analyze(proj, config_dir=cfg)
        chk("配布物の hooks.json を固定表より優先（新名 1 本だけが潜在・出所は配布物）",
            (len(r["latent"]), r["latent_source"].endswith(os.path.join("willink-kit", "hooks", "hooks.json"))), (1, True))
        os.remove(os.path.join(proj, ".claude", "willink-kit", "hooks", "hooks.json"))

        # --- --kit-hooks で cache を差し替えられる（consumer CI・registry 無しでも測れる） -------
        registry([{"scope": "project", "projectPath": proj, "installPath": cache_new, "version": "2.7.0"}])
        _write(os.path.join(proj, ".claude", "settings.json"), _settings(two, {DEFAULT_PLUGIN: True}))
        r = analyze(proj, kit_hooks=os.path.join(cache_new, "hooks", "hooks.json"), config_dir=os.path.join(tmp, "nowhere"))
        chk("--kit-hooks 指定: registry 無しでも hooks.json を直接読む → exit 1", r["exit"], 1)

        # --- 検出側の自己検査: 変異体（/hooks/<name>.sh を呼ばない command）は 0 本 -----------
        mut = os.path.join(tmp, "mut.json")
        _write(mut, {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "echo noop"}]}]}})
        r = analyze(proj, kit_hooks=mut, config_dir=os.path.join(tmp, "nowhere"))
        chk("検出側: hook を呼ばない変異体の hooks.json は 0 本（偽の赤を出さない）", (len(r["plugin_hooks"]), r["exit"]), (0, 0))

    print("\n検査 %d 件: 合格 %d / 不合格 %d" % (n_pass + n_fail, n_pass, n_fail))
    return 0 if n_fail == 0 else 1


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Core hook の配線経路が 1 本か（plugin と inline の二重登録）を実測する")
    ap.add_argument("--target", help="consumer リポのルート（既定: PH_TARGET_ROOT → git toplevel → cwd）")
    ap.add_argument("--plugin", default=DEFAULT_PLUGIN, help="plugin 経路の名前（既定: %(default)s）")
    ap.add_argument("--kit-hooks", help="plugin の hooks.json を直接指定（installed_plugins.json を引かない）")
    ap.add_argument("--config-dir", help="Claude Code の設定ディレクトリ（既定: $CLAUDE_CONFIG_DIR → ~/.claude）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    target = os.path.abspath(a.target) if a.target else target_root()
    try:
        r = analyze(target, plugin=a.plugin, kit_hooks=a.kit_hooks, config_dir=a.config_dir)
    except Exception as e:  # noqa: BLE001 — 落ちて rc 1 になると呼び側（install.sh）が ❌ 行を探して黙る
        r = {"target": target, "plugin": a.plugin, "exit": 2,
             "note": "想定外の例外で測れていない（%s: %s）" % (type(e).__name__, e)}
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(render(r))
    return r["exit"]


if __name__ == "__main__":
    sys.exit(main())
