#!/usr/bin/env python3
"""oneshot-preflight.py — /oneshot の契約の検査・red-first・毎周の判定・mutation（exit code が唯一の真実）

なぜ在るか（2026-09-25・docs/design/oneshot-mode.md §2〜§4・§8-1）:
  /oneshot は細かく詰めた仕様 1 本から PR まで、人がループの外から見守る中で無人で回す。
  無人区間の品質は **契約に書いた DoD の品質と同じ** にしかならないので、
  「完了」はモデルの自己申告ではなく、ここが返す exit code だけで決める。
  このスクリプト単体でも価値がある（/build の検証にもそのまま使える）。

サブコマンド（出力は分母つき）:
  check  [--contract P]   契約の検査。0 = OK / 1 = Phase −1（対話）へ戻す問題 / 2 = 拒否・読めない
  start                   無人区間に入る。check → 既定ブランチ上なら拒否 → spec.md の有無 → red-first
                          （dod が全部緑、または新規の判定が実装前から緑なら state を作らず exit 1）
                          → oneshot/state.json を作る（status=running）。0 / 1 / 2
  round                   goal-loop の --check に渡す 1 周の判定。dod を全部実行し、新規は実行件数 > 0、
                          forbid（契約・gate 設定・scope 外）への変更 0。全部満たせば 0、それ以外 1、state が無ければ 2
  boundary                周の境界。oneshot/STOP → status=stopped で 3 / 予算（minutes・tokens）超過 → 2（CAP）/ 0
  mutate                  直近の round が全部緑のときだけ。新規の判定の mutate を 1 本ずつ当てて赤を確かめ、戻す。
                          全部赤で 0 / 緑・無効果・失敗が 1 本でもあれば 1 / 戻せなかったら 2
  resume                  人が契約を直した後の再開（status が escalated / stopped のときだけ）。契約を検査し直し、
                          契約・spec・gate 設定のハッシュを記録し直して status=running に戻す。0 / 1 / 2
  note  [--phase P] [--text T] [--not-done T] [--finding T] [--blocker T] [--status S] [--pr URL]
                          モデルが Write を使わずに state を更新する口（oneshot/ は forbid なので Write では書けない）。
                          delivered は「直近 round 全部緑・違反 0・mutation 全部赤・PR あり・やらなかったこと 1 件以上」でなければ 1
  status                  1 行の進捗。state が無ければ 2
  --self-test

トークンの数え方（§7-4）: post-oneshot-elapsed.sh が記録した transcript と、同じ session の subagent の transcript
（<dir>/<session>/subagents/*.jsonl）から、started_at 以降の assistant メッセージの usage を message.id ごとに 1 回だけ足す。
used = input_tokens + cache_creation_input_tokens + output_tokens（cache_read は含めない）。取れなければ used=null（0 と書かない）。

環境変数: ONESHOT_CMD_TIMEOUT（dod / mutate 1 本の秒・既定 1800）・PH_TARGET_ROOT（対象リポ）
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _oneshot as O  # noqa: E402

PHASES = ("preflight", "explore", "plan", "implement", "verify", "loop", "mutate", "deliver", "escalate")
STATUSES = ("running", "delivered", "escalated", "stopped")
# note で書ける status。running に戻せるのは resume（人）だけ、stopped は boundary（人が置いた STOP）だけ
NOTE_STATUSES = ("delivered", "escalated")
# oneshot/ 配下のうち、周の判定で「変更」と数えない実行時ファイル（spec.md と oneshot.yaml はハッシュで守る）
RUNTIME = ("oneshot/state.json", "oneshot/state.json.tmp", "oneshot/.hooks.json", "oneshot/.hooks.json.tmp",
           "oneshot/STOP", "oneshot/plan.md")
RUNTIME_PREFIX = ("oneshot/.state",)
EXCLUDE_LINES = ("oneshot/state.json", "oneshot/.hooks.json", "oneshot/.state*", "oneshot/STOP", "oneshot/plan.md", "oneshot/*.tmp")
TOKENS_HOW = ("input_tokens + cache_creation_input_tokens + output_tokens の和（cache_read は含めない）。"
              "本体と subagent の transcript・started_at 以降・message.id ごとに 1 回")


def out(msg=""):
    print(msg, flush=True)


def git(root, *args, env=None, check=False):
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, env=e)
    if check and r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr.strip()))
    return r


def timeout_s():
    try:
        return max(1, int(os.environ.get("ONESHOT_CMD_TIMEOUT", "1800")))
    except ValueError:
        return 1800


def run_cmd(cmd, root):
    """(exit, 出力)。タイムアウトは exit=None。タイムアウトしたら bash の子孫までプロセスグループごと止める
    （bash だけ止めると test runner が裏で走り続け、次の周の結果に混ざる）。"""
    p = subprocess.Popen(["bash", "-c", cmd], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace", start_new_session=True)
    try:
        so, _ = p.communicate(timeout=timeout_s())
        return p.returncode, so or ""
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        so, _ = p.communicate()
        return None, so or ""


def run_dod(c, root):
    res = []
    for e in c.get("dod") or []:
        kind, cmd = e.get("kind"), e.get("cmd") or ""
        code, text = run_cmd(cmd, root)
        ent = {"kind": kind, "cmd": cmd, "result": "red", "exit": code, "count": None, "reason": ""}
        if code is None:
            ent["reason"] = "タイムアウト（%ds）" % timeout_s()
        elif code != 0:
            ent["reason"] = "exit %d" % code
        if kind == "new":
            cnt = O.count_from_output(str(e.get("count") or ""), text)
            ent["count"] = cnt
            if code == 0:
                if cnt is None:
                    ent["reason"] = "実行件数が取れない（count が出力に一致しない）— 0 件と同じく赤"
                elif cnt <= 0:
                    ent["reason"] = "実行件数 0 件 — 何も測っていない"
                else:
                    ent["result"] = "green"
        elif code == 0:
            ent["result"] = "green"
        res.append(ent)
    return res


def green_of(dod_res):
    return sum(1 for e in dod_res if e["result"] == "green")


def load_or_die(root, path=None):
    c, err = O.load_contract(root, path)
    if c is None:
        out("❓ " + err)
        sys.exit(2)
    return c


def state_or_die(root):
    st = O.load_state(root)
    if st is None:
        out("❓ oneshot/state.json が無い・読めない — 無人区間に入っていない（start を先に）")
        sys.exit(2)
    return st


# ─────────────────────────── check ───────────────────────────

def do_check(root, contract=None, quiet=False):
    c, err = O.load_contract(root, contract)
    if c is None:
        out("❓ " + err + "（exit 2）")
        return 2, None
    probs = O.validate_contract(c)
    dod = [e for e in (c.get("dod") or []) if isinstance(e, dict)]
    kinds = {k: sum(1 for e in dod if e.get("kind") == k) for k in O.KINDS}
    rej = [p for p in probs if p["level"] == "reject"]
    ph1 = [p for p in probs if p["level"] != "reject"]
    if not quiet or probs:
        out("# oneshot check — dod %d 本（new %d / regression %d / boundary %d）・問題 %d 件（拒否 %d / Phase −1 %d）"
            % (len(dod), kinds["new"], kinds["regression"], kinds["boundary"], len(probs), len(rej), len(ph1)))
        for p in rej:
            out("  ⛔ [%s] %s" % (p["code"], p["msg"]))
        for p in ph1:
            out("  ↩️  [%s] %s" % (p["code"], p["msg"]))
    if rej:
        out("⛔ 拒否（exit 2）— 無人の oneshot には入れない")
        return 2, c
    if ph1:
        out("↩️  Phase −1（対話）へ戻して契約を直す（exit 1）")
        return 1, c
    if not quiet:
        out("✅ 契約は起動条件を満たす")
    return 0, c


# ─────────────────────────── start ───────────────────────────

def default_branches(root):
    names = {"main", "master"}
    r = git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if r.returncode == 0 and r.stdout.strip():
        names.add(r.stdout.strip().split("/", 1)[-1])
    return names


def snapshot_paths(root):
    """作業ツリーで base から変わっている / 未追跡のパス。"""
    r1 = git(root, "diff", "--no-renames", "--name-only", "HEAD")
    r2 = git(root, "ls-files", "--others", "--exclude-standard")
    return sorted(set(x for x in (r1.stdout + "\n" + r2.stdout).splitlines() if x.strip()))


def add_excludes(root):
    r = git(root, "rev-parse", "--git-path", "info/exclude")
    if r.returncode != 0:
        return None
    p = r.stdout.strip()
    if not os.path.isabs(p):
        p = os.path.join(root, p)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        cur = open(p, encoding="utf-8").read().splitlines()
    except OSError:
        cur = []
    add = [x for x in EXCLUDE_LINES if x not in cur]
    if add:
        with open(p, "a", encoding="utf-8") as fh:
            if cur and cur[-1] != "":
                fh.write("\n")
            fh.write("# /oneshot の実行時ファイル（oneshot-preflight start が追記）\n")
            fh.write("\n".join(add) + "\n")
    return p


def protected_paths(c, root=None):
    """ハッシュ（git の blob）で守るファイル。gate_config のうちディレクトリ・glob はここに入れず、
    差分の分類（classify_path → gate）で守る（ディレクトリに hash-object は当てられない）。"""
    files = []
    for g in O.gate_config(c):
        if g.endswith("/") or any(ch in g for ch in "*?[") or (root and os.path.isdir(os.path.join(root, g))):
            continue
        files.append(g)
    return list(dict.fromkeys([O.CONTRACT, "oneshot/spec.md"] + files))


def blob_at(root, ref, rel):
    """ref の時点の rel の blob（無ければ None）。"""
    r = git(root, "rev-parse", "--verify", "--quiet", "%s:%s" % (ref, rel))
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def blob_now(root, rel):
    """作業ツリーの rel を blob にしたときの sha（無ければ None）。hash-object は clean フィルタも通す。"""
    if not os.path.lexists(os.path.join(root, rel)):
        return None
    r = git(root, "hash-object", "--", rel)
    return r.stdout.strip() if r.returncode == 0 else "❓"


def spec_matches_contract(root):
    """spec.md に書かれた契約の sha256（先頭 16 桁）が今の oneshot.yaml と一致するか。(ok, 理由)"""
    try:
        body = open(os.path.join(root, "oneshot", "spec.md"), encoding="utf-8").read()
    except OSError:
        return False, "oneshot/spec.md が読めない"
    m = re.search(r"sha256 `([0-9a-f]{16})`", body)
    cur = (O.sha256_file(os.path.join(root, O.CONTRACT)) or "")[:16]
    if not m:
        return False, "spec.md に契約の sha256 が無い（oneshot-spec.py spec で作り直す）"
    if m.group(1) != cur:
        return False, "spec.md（%s）と今の oneshot.yaml（%s）が違う — 人が承認した後に契約が変わった。spec を作り直して人に見せる" % (m.group(1), cur)
    return True, ""


def contract_committed(root, c):
    """契約・spec・gate 設定が commit 済みで、作業ツリーと一致しているか。(ok, 理由)"""
    paths = [p for p in protected_paths(c, root) if os.path.lexists(os.path.join(root, p))]
    for p in [O.CONTRACT, "oneshot/spec.md"]:
        if git(root, "ls-files", "--error-unmatch", "--", p).returncode != 0:
            return False, "%s が commit されていない（人が承認した契約を commit してから）" % p
    st = git(root, "status", "--porcelain", "--", *paths)
    if st.returncode != 0 or st.stdout.strip():
        return False, "契約・spec・gate 設定に commit していない変更がある: %s" % st.stdout.strip().replace("\n", " / ")
    return True, ""


def do_start(root):
    rc, c = do_check(root)
    if rc != 0:
        return rc
    br = git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if br == "HEAD" or not br:
        out("⛔ detached HEAD では無人区間に入らない — 短命ブランチを切って走らせる（PR を開く先が要る・exit 2）")
        return 2
    if br in default_branches(root):
        out("⛔ 既定ブランチ（%s）の上では無人区間に入らない — worktree を切って短命ブランチで走らせる（exit 2）" % br)
        return 2
    st = O.load_state(root)
    if st is not None and st.get("status") == "running":
        out("⛔ status=running の state がある — 別の oneshot が走っている（初版は並列非対応・exit 2）")
        return 2
    if st is not None and st.get("status") != "delivered":
        # 止まった run（escalated / stopped）の上で start すると、周の数・予算・基準点が初期化され、それまでに
        # commit した柵の外の変更が基準点に入って見えなくなる（= 自分で再開するのと同じ）。再開は resume（人）だけ
        out("⛔ 前回の run が終わっていない（status=%s）— start し直さない。再開は人が resume。やめるなら人が "
            "oneshot/state.json を片付けてから（exit 2）" % st.get("status"))
        return 2
    if not os.path.isfile(os.path.join(root, "oneshot", "spec.md")):
        out("↩️  oneshot/spec.md が無い — 人が読んで「走らせてよい」と言える文書が要る（oneshot-spec.py で作る・exit 1）")
        return 1
    ok, why = spec_matches_contract(root)
    if not ok:
        out("↩️  %s（exit 1）" % why)
        return 1
    ok, why = contract_committed(root, c)
    if not ok:
        out("↩️  %s（exit 1）" % why)
        return 1
    ex0 = add_excludes(root)   # 前回の実行時ファイルを「汚れ」と数えないよう、clean の判定より先に
    dirty = [x for x in snapshot_paths(root) if not is_runtime(x)]
    if dirty:
        out("↩️  作業ツリーが clean でない（%d パス: %s）— 無人区間は commit 済みの基準点から始める。commit するか戻してから start（exit 1）"
            % (len(dirty), ", ".join(dirty[:8]) + (" …" if len(dirty) > 8 else "")))
        return 1
    base = git(root, "rev-parse", "HEAD")
    if base.returncode != 0:
        out("❓ HEAD が読めない — commit の無いリポでは差分を測れない（exit 2）")
        return 2
    dod_res = run_dod(c, root)
    total, green = len(dod_res), green_of(dod_res)
    new_green = [i for i, e in enumerate(dod_res) if e["kind"] == "new" and e["result"] == "green"]
    n_new = sum(1 for e in dod_res if e["kind"] == "new")
    out("# red-first — dod %d 本中 %d 本 red（new %d 本中 %d 本 red）" % (total, total - green, n_new, n_new - len(new_green)))
    for i, e in enumerate(dod_res):
        out("  %s dod[%d] %s `%s`%s" % ("🟢" if e["result"] == "green" else "🔴", i, e["kind"], e["cmd"],
                                        ("  — " + e["reason"]) if e["reason"] else ""))
    if green == total:
        out("↩️  実装前から dod が全部緑 — 「やることが無い」か「測定器が壊れている」。新規の判定（kind=new）を先に書き、"
            "赤になることを確かめてから走らせる（state は作らない・exit 1）")
        return 1
    if new_green:
        out("↩️  新規の判定が実装前から緑: %s — この変更を測れていない。変更が無ければ赤になる判定に直す（state は作らない・exit 1）"
            % ", ".join("dod[%d]" % i for i in new_green))
        return 1
    if st is not None:
        # 前回（delivered）の state の退避は、全部の検査を通った後だけ（red-first で落ちても state を消さない）
        prev = os.path.join(root, "oneshot", ".state-prev-%d.json" % int(time.time()))
        os.replace(O.state_path(root), prev)
        out("ℹ️  前回の state（status=%s）を %s へ退避" % (st.get("status"), os.path.relpath(prev, root)))
    b = c.get("budget") or {}
    st = {
        "schema": 1, "status": "running", "goal": c.get("goal"), "started_at": O.now_iso(),
        "budget": {k: b.get(k) for k in ("attempts", "minutes", "tokens")}, "blocker": "",
        "phase": "preflight", "round": 0, "base_ref": base.stdout.strip(), "contract_ref": base.stdout.strip(),
        "protected": {p: blob_at(root, base.stdout.strip(), p) for p in protected_paths(c, root)},
        "base_dirty": {},
        "dod": dod_res, "red_first": {"at": O.now_iso(), "red": total - green, "total": total},
        "mutation": [], "tokens": {"used": None, "how": TOKENS_HOW}, "note": "", "last_diff": "",
        "history": [], "violations": [], "not_done": [], "findings": [], "pr": None, "interrupted": None,
    }
    O.save_state(root, st)
    ex = ex0 or add_excludes(root)
    out("✅ 無人区間に入った — oneshot/state.json を作成（status=running・base %s）%s"
        % (st["base_ref"][:7], ("・exclude: " + os.path.relpath(ex, root)) if ex else "・❓ exclude に書けなかった"))
    return 0


# ─────────────────────────── round ───────────────────────────

def is_runtime(rel):
    return rel in RUNTIME or rel.startswith(RUNTIME_PREFIX)


def protected_changes(root, st):
    """契約・spec・gate 設定のうち、contract_ref（start / resume の時点の commit）から変わったもの。
    基準は git の履歴から取る（state.json の値は判定される側が書けるので基準にしない）。"""
    ref = st.get("contract_ref") or st.get("base_ref") or "HEAD"
    return [p for p in (st.get("protected") or {}) if blob_now(root, p) != blob_at(root, ref, p)]


def forbid_violations(root, c, st):
    v = []
    for p in protected_changes(root, st):
        v.append("契約・gate 設定が変わった: %s（無人区間では変えない。変えるなら escalate して人が直し resume）" % p)
    base = st.get("base_ref") or "HEAD"
    # 作業ツリー（未 commit を含む）と HEAD（push される commit）の両方を見る。commit した後で作業ツリーだけ
    # 戻しても PR には載るので、片方だけでは足りない。rename は検出しない（`git mv` で forbid から scope へ
    # 移すと --name-only は移動先しか出さず、元の場所の削除が見えなくなる）
    r1 = git(root, "diff", "--no-renames", "--name-only", base)
    r3 = git(root, "diff", "--no-renames", "--name-only", base, "HEAD")
    r2 = git(root, "ls-files", "--others", "--exclude-standard")
    for r, what in ((r1, "作業ツリー"), (r3, "HEAD"), (r2, "未追跡")):
        if r.returncode != 0:
            v.append("❓ base %s との差分（%s）が取れない: %s" % (base[:7], what, r.stderr.strip()))
    paths = sorted(set(x for x in (r1.stdout + "\n" + r3.stdout + "\n" + r2.stdout).splitlines() if x.strip()))
    prot = set((st.get("protected") or {}).keys())
    bd = st.get("base_dirty") or {}
    scanned = 0
    for rel in paths:
        if is_runtime(rel) or rel in prot:
            continue
        if rel in bd and O.sha256_file(os.path.join(root, rel)) == bd[rel]:
            continue
        scanned += 1
        cls = O.classify_path(c, rel)
        if cls != "writable":
            hint = ("（生成物なら .gitignore に足す・要る変更なら scope に足す — どちらも人が決める。"
                    "戻せるなら戻す。人が要るなら escalate）") if cls == "out-of-scope" else ""
            v.append("%s: %s%s" % ({"forbid": "forbid への変更", "gate": "gate 設定への変更",
                                    "out-of-scope": "scope 外への変更"}[cls], rel, hint))
    return v, scanned


def diff_stat(root, base):
    r = git(root, "diff", "--shortstat", base)
    u = [x for x in git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
         if x.strip() and not is_runtime(x)]
    s = r.stdout.strip() or "0 files changed"
    return "%s（未追跡 %d）" % (s, len(u))


def fmt_elapsed(st):
    t0 = O.parse_iso(st.get("started_at"))
    if t0 is None:
        return "経過 ❓", None
    sec = int(time.time() - t0)
    mins = (st.get("budget") or {}).get("minutes")
    return ("経過 %dm / %dm" % (sec // 60, mins) if mins else "経過 %dm" % (sec // 60)), sec


def fmt_tokens(st):
    used = (st.get("tokens") or {}).get("used")
    bud = (st.get("budget") or {}).get("tokens")
    if used is None:
        return "tokens ❓ / %s" % (bud if bud else "?")
    return "tokens %dk / %sk" % (used // 1000, (bud // 1000) if isinstance(bud, int) else "?")


def do_round(root):
    st = state_or_die(root)
    if st.get("status") != "running":
        out("⛔ status=%s — 無人区間ではない（exit 2）" % st.get("status"))
        return 2
    c = load_or_die(root)
    dod_res = run_dod(c, root)
    v, scanned = forbid_violations(root, c, st)
    total, green = len(dod_res), green_of(dod_res)
    st["dod"] = dod_res
    st["round"] = int(st.get("round") or 0) + 1
    st["violations"] = v
    st["last_diff"] = diff_stat(root, st.get("base_ref") or "HEAD")
    st["tokens"] = {"used": count_tokens(root, st), "how": TOKENS_HOW}
    st.pop("verified_tree", None)
    if green == total and not v:
        # mutate と delivered は「この木」を検証済みとして扱う。round の後に木が変われば、検証していない
        try:
            st["verified_tree"] = tree_of(root)
        except RuntimeError as e:
            v.append("❓ 緑になった作業ツリーを記録できない: %s" % e)
            st["violations"] = v
    st.setdefault("history", []).append({"round": st["round"], "at": O.now_iso(), "green": green, "total": total,
                                         "violations": len(v)})
    O.save_state(root, st)
    el, _ = fmt_elapsed(st)
    out("oneshot round %d: dod %d/%d green・違反 %d 件（変更 %d パス走査）・%s・%s・%s"
        % (st["round"], green, total, len(v), scanned, el, fmt_tokens(st), st["last_diff"]))
    for i, e in enumerate(dod_res):
        if e["result"] != "green":
            out("  🔴 dod[%d] %s `%s` — %s" % (i, e["kind"], e["cmd"], e["reason"] or "赤"))
    for x in v:
        out("  ⛔ " + x)
    att = (st.get("budget") or {}).get("attempts")
    if isinstance(att, int) and st["round"] > att:
        out("  🛑 試行の上限を超えている（周 %d / 上限 %d）— boundary が CAP を返す。escalate して人へ" % (st["round"], att))
    return 0 if (green == total and not v) else 1


# ─────────────────────────── boundary ───────────────────────────

def do_boundary(root):
    st = state_or_die(root)
    if os.path.exists(os.path.join(root, "oneshot", "STOP")):
        st["status"] = "stopped"
        st["finished_at"] = O.now_iso()
        st["interrupted"] = {"round": st.get("round"), "phase": st.get("phase"), "at": O.now_iso()}
        O.save_state(root, st)
        out("✋ oneshot/STOP がある — 人の割り込み。status=stopped（周 %s・phase %s）。/build の該当 phase へ移る（exit 3）"
            % (st.get("round"), st.get("phase")))
        return 3
    if st.get("status") != "running":
        out("ℹ️  status=%s — 無人区間ではない（exit 0）" % st.get("status"))
        return 0
    b = st.get("budget") or {}
    el, sec = fmt_elapsed(st)
    used = count_tokens(root, st)
    st["tokens"] = {"used": used, "how": TOKENS_HOW}
    O.save_state(root, st)
    caps = []
    rnd = int(st.get("round") or 0)
    if isinstance(b.get("attempts"), int) and rnd >= b["attempts"]:
        caps.append("試行の上限に達した（周 %d / 上限 %d）— goal-loop の state を消しても数え直さない" % (rnd, b["attempts"]))
    if sec is not None and isinstance(b.get("minutes"), int) and sec > b["minutes"] * 60:
        caps.append("時間の予算を超えた（%ds > %ds）" % (sec, b["minutes"] * 60))
    if used is not None and isinstance(b.get("tokens"), int) and used > b["tokens"]:
        caps.append("トークンの予算を超えた（%d > %d）" % (used, b["tokens"]))
    line = "oneshot boundary: 周 %s・%s・%s" % (st.get("round"), el, fmt_tokens(st))
    if caps:
        out(line)
        for x in caps:
            out("  🛑 CAP: " + x)
        out("  → §3-7 escalate: 未達の dod と最後の差分を draft PR で出し、"
            "`oneshot-preflight.py note --status escalated --blocker \"<理由 1 行>\"` で止まる（exit 2）")
        return 2
    if used is None:
        line += "（❓ トークンは測れていない — CAP の判定に使っていない）"
    out(line)
    return 0


# ─────────────────────────── tokens ───────────────────────────

def transcript_files(root):
    side = {}
    try:
        with open(os.path.join(root, "oneshot", ".hooks.json"), encoding="utf-8") as fh:
            side = json.load(fh)
    except (OSError, ValueError):
        return []
    tp = side.get("transcript_path") if isinstance(side, dict) else None
    if not isinstance(tp, str) or not os.path.isfile(tp):
        return []
    files = [tp]
    stem = tp[:-6] if tp.endswith(".jsonl") else tp
    files += sorted(glob.glob(os.path.join(stem, "subagents", "*.jsonl")))
    return files


def count_tokens(root, st):
    files = transcript_files(root)
    if not files:
        return None
    t0 = O.parse_iso(st.get("started_at"))
    if t0 is None:
        return None
    per = {}
    readable = 0
    for f in files:
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        readable += 1
        with fh:
            for n, line in enumerate(fh):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "assistant":
                    continue
                ts = O.parse_iso(d.get("timestamp"))
                if ts is None or ts < t0:
                    continue
                m = d.get("message") or {}
                u = m.get("usage") or {}
                if not isinstance(u, dict):
                    continue
                key = (f, m.get("id") or d.get("requestId") or n)
                cur = per.setdefault(key, {})
                for k in ("input_tokens", "cache_creation_input_tokens", "output_tokens"):
                    v = u.get(k)
                    if isinstance(v, int):
                        cur[k] = max(cur.get(k, 0), v)
    if readable == 0:
        return None
    return sum(sum(v.values()) for v in per.values())


# ─────────────────────────── mutate ───────────────────────────

def tree_of(root):
    """作業ツリー全体（追跡 + 未追跡・ignore 除く）の木。本物の index には触らない。"""
    fd, idx = tempfile.mkstemp(prefix="oneshot-idx-")
    os.close(fd)
    os.unlink(idx)
    # 本物の index の写しから始める（空の index からだと core.fileMode=false の実行ビット・sparse checkout・
    # 初期化していない submodule が HEAD の木と一致しない。全ファイルを hash し直さないので速い）
    real = git(root, "rev-parse", "--git-path", "index").stdout.strip()
    if real and not os.path.isabs(real):
        real = os.path.join(root, real)
    if real and os.path.isfile(real):
        shutil.copyfile(real, idx)
    try:
        env = {"GIT_INDEX_FILE": idx}
        git(root, "add", "-A", "--", ".", env=env, check=True)
        return git(root, "write-tree", env=env, check=True).stdout.strip()
    finally:
        for p in (idx, idx + ".lock"):
            if os.path.exists(p):
                os.unlink(p)


def tree_diff(root, a, b):
    r = git(root, "diff-tree", "-r", "--no-renames", "--name-only", a, b, check=True)
    return [x for x in r.stdout.splitlines() if x.strip()]


def restore_paths(root, tree, paths):
    for rel in paths:
        dst = os.path.join(root, rel)
        ls = git(root, "ls-tree", tree, "--", rel).stdout.strip()
        if not ls:
            if os.path.islink(dst) or os.path.isfile(dst):
                os.unlink(dst)
            elif os.path.isdir(dst):
                shutil.rmtree(dst)
            d = os.path.dirname(dst)
            while d and d != root and os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
                d = os.path.dirname(d)
            continue
        mode, _typ, rest = ls.split(" ", 2)
        sha = rest.split("\t", 1)[0]
        data = subprocess.run(["git", "cat-file", "blob", sha], cwd=root, capture_output=True, check=True).stdout
        if os.path.islink(dst) or os.path.isfile(dst):
            os.unlink(dst)
        os.makedirs(os.path.dirname(dst) or root, exist_ok=True)
        if mode == "120000":
            os.symlink(data.decode(), dst)
        else:
            with open(dst, "wb") as fh:
                fh.write(data)
            os.chmod(dst, 0o755 if mode == "100755" else 0o644)


def do_mutate(root):
    st = state_or_die(root)
    if st.get("status") != "running":
        out("⛔ status=%s — 無人区間ではない（exit 2）" % st.get("status"))
        return 2
    hist = st.get("history") or []
    last = hist[-1] if hist else None
    if not last or last.get("green") != last.get("total") or last.get("violations"):
        out("↩️  直近の round が全部緑・違反 0 でない — mutation は緑になった後に当てる（round を先に・exit 1）")
        return 1
    pc = protected_changes(root, st)
    if pc:
        out("⛔ 契約・gate 設定が round の後に変わった: %s — 無人区間では変えない（escalate して人が直し resume・exit 1）"
            % ", ".join(pc))
        return 1
    try:
        now_tree = tree_of(root)
    except RuntimeError as e:
        out("❓ 作業ツリーを読めない: %s（exit 2）" % e)
        return 2
    if not st.get("verified_tree") or now_tree != st.get("verified_tree"):
        out("↩️  round で緑になった後に作業ツリーが変わった — 検証していない木に mutation を当てない。round をやり直す（exit 1）")
        return 1
    c = load_or_die(root)
    news = [(i, e) for i, e in enumerate(c.get("dod") or []) if isinstance(e, dict) and e.get("kind") == "new"]
    try:
        t0 = tree_of(root)
    except RuntimeError as e:
        out("❓ 作業ツリーを退避できない: %s（exit 2）" % e)
        return 2
    results, findings = [], []
    for i, e in news:
        mut = str(e.get("mutate") or "")
        mcode, mtext = run_cmd(mut, root)
        t1 = tree_of(root)
        changed = tree_diff(root, t0, t1)
        if mcode != 0:
            r = "error"
        elif not changed:
            r = "no-effect"
        else:
            ccode, _ = run_cmd(str(e.get("cmd") or ""), root)
            # タイムアウトは「赤になった」ではなく「測れなかった」（壊したら止まらなくなった可能性もある）
            r = "error" if ccode is None else ("green" if ccode == 0 else "red")
            if ccode is None:
                mcode, mtext = "timeout", "判定がタイムアウトした（%ds）— 赤と数えない" % timeout_s()
        t2 = tree_of(root)
        to_restore = tree_diff(root, t0, t2)
        restore_paths(root, t0, to_restore)
        if tree_of(root) != t0:
            out("⛔ dod[%d] の mutate を戻せなかった — 作業ツリーが元と一致しない（%s）。人が確かめる（exit 2）"
                % (i, ", ".join(tree_diff(root, t0, tree_of(root)))[:300]))
            st["findings"] = (st.get("findings") or []) + ["mutate を戻せなかった: dod[%d]" % i]
            st["blocker"] = "mutate の後に作業ツリーを元へ戻せなかった（dod[%d]）— 人が確かめる" % i
            O.save_state(root, st)
            return 2
        results.append({"index": i, "mutate": mut, "result": r, "changed": changed})
        if r != "red":
            msg = {"green": "壊しても緑のまま — 測定器が壊れている（実装を直しても意味がない）",
                   "no-effect": "mutate が何も変えなかった — 当たる先の文字列かパスが違う（sed の -i の違い等）",
                   "error": "mutate 自体が失敗した（exit %s）%s" % (mcode, (" — " + mtext.strip().splitlines()[-1][:120]) if mtext.strip() else "")}[r]
            findings.append("dod[%d] `%s`: %s" % (i, e.get("cmd"), msg))
    st["mutation"] = results
    st["findings"] = (st.get("findings") or []) + findings
    st["phase"] = "mutate"
    O.save_state(root, st)
    n_red = sum(1 for x in results if x["result"] == "red")
    out("# mutation-first — new %d 本中 %d 本が壊すと赤（作業ツリーは元に戻した・本物の index は触らない・ignore 対象のファイルは退避の対象外）" % (len(results), n_red))
    for x in results:
        out("  %s dod[%d] %s（変えたパス %d）" % ("🔴" if x["result"] == "red" else "⚠️ ", x["index"], x["result"], len(x["changed"])))
    for f in findings:
        out("  所見: " + f)
    if not results:
        out("❓ new の dod が 0 本 — mutation を当てる対象が無い（exit 1）")
        return 1
    return 0 if n_red == len(results) else 1


# ─────────────────────────── note / status ───────────────────────────

def do_resume(root, accept=None):
    # resume は人だけ。Claude Code の Bash には CLAUDECODE=1 が入るので、そこから呼ばれたら拒否する
    # （hook の正規表現は argparse の書き方で外せるので、道具自身でも止める。人は自分の端末で呼ぶ）
    if os.environ.get("CLAUDECODE") == "1":
        out("⛔ resume は人だけが呼ぶ — Claude Code の中（CLAUDECODE=1）からは実行しない。人が自分の端末で実行する（exit 2）")
        return 2
    st = state_or_die(root)
    if st.get("status") not in ("escalated", "stopped"):
        out("⛔ status=%s — resume は escalated / stopped（人がループに戻った後）のときだけ（exit 2）" % st.get("status"))
        return 2
    rc, c = do_check(root, quiet=True)
    if rc != 0:
        return rc
    ok, why = spec_matches_contract(root)
    if not ok:
        out("↩️  %s（exit 1）" % why)
        return 1
    ok, why = contract_committed(root, c)
    if not ok:
        out("↩️  %s（exit 1）" % why)
        return 1
    changed = protected_changes(root, st)
    # 止まっている間に人が加えた scope 外 / forbid の変更（例: 生成物を .gitignore に足す）のうち、
    # **人が --accept で名指ししたものだけ**を承認し、以後の round で数えない（中身がそのままの間だけ）。
    # 名指ししていない変更は違反のまま残る（誰が加えたか機械には分からないので、黙って全部は受けない）。
    # resume は人だけが呼ぶ（pre-oneshot-scope.sh が Claude のツール経由の resume を止める）。
    base = st.get("base_ref") or "HEAD"
    paths = set(git(root, "diff", "--no-renames", "--name-only", base).stdout.splitlines()) | set(
        git(root, "diff", "--no-renames", "--name-only", base, "HEAD").stdout.splitlines()) | set(
        git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines())
    prot = set(protected_paths(c, root))
    outside = sorted(x for x in paths if x.strip() and not is_runtime(x) and x not in prot
                     and O.classify_path(c, x) != "writable")
    want = [O._norm(x) for x in (accept or [])]
    unknown = [x for x in want if x not in outside]
    if unknown:
        out("↩️  --accept で名指ししたが、scope 外の変更として見当たらない: %s（exit 1）" % ", ".join(unknown))
        return 1
    bd = st.setdefault("base_dirty", {})
    accepted = []
    for rel in want:
        bd[rel] = O.sha256_file(os.path.join(root, rel))
        accepted.append(rel)
    left = [x for x in outside if x not in bd]
    st["contract_ref"] = git(root, "rev-parse", "HEAD").stdout.strip()
    st["protected"] = {p: blob_at(root, st["contract_ref"], p) for p in protected_paths(c, root)}
    st["budget"] = {k: (c.get("budget") or {}).get(k) for k in ("attempts", "minutes", "tokens")}
    st["dod"] = [{"kind": e.get("kind"), "cmd": e.get("cmd"), "result": "未実行", "exit": None, "count": None, "reason": ""}
                 for e in c.get("dod") or [] if isinstance(e, dict)]
    st.setdefault("findings", []).append("resume %s: 人が直して再開（契約・gate 設定で変わったもの: %s／人が承認した scope 外の変更 %d 件: %s）"
                                         % (O.now_iso(), ", ".join(changed) or "なし", len(accepted), ", ".join(accepted) or "なし"))
    st["status"], st["blocker"], st["mutation"] = "running", "", []
    st.pop("finished_at", None)
    O.save_state(root, st)
    out("✅ 再開 — status=running（契約の基準を %s に移した: 契約・gate 設定で変わったもの %d 件／scope 外の変更 %d 件のうち "
        "%d 件を人の承認として以後数えない%s）。round から続ける"
        % (st["contract_ref"][:7], len(changed), len(outside), len(accepted), (": " + ", ".join(accepted)) if accepted else ""))
    if left:
        out("  ⚠️  承認していない scope 外の変更 %d 件は round で違反のまま: %s（承認するなら --accept <path>）" % (len(left), ", ".join(left)))
    att, rnd = (st.get("budget") or {}).get("attempts"), int(st.get("round") or 0)
    if isinstance(att, int) and rnd >= att:
        out("  ⚠️  周 %d / 上限 %d — このままでは次の boundary で CAP になる（続けるなら契約の attempts を上げて spec を作り直す）" % (rnd, att))
    return 0


def do_note(root, a):
    st = state_or_die(root)
    if a.phase:
        if a.phase not in PHASES:
            out("⛔ phase は %s のどれか（exit 2）" % " / ".join(PHASES))
            return 2
        st["phase"] = a.phase
    if a.text is not None:
        st["note"] = a.text.strip().splitlines()[0][:300] if a.text.strip() else ""
    for x in a.not_done or []:
        st.setdefault("not_done", []).append(x)
    for x in a.finding or []:
        st.setdefault("findings", []).append(x)
    if a.blocker is not None:
        st["blocker"] = a.blocker.strip()
    if a.pr:
        st["pr"] = a.pr
    # Checker（dev-reviewer / dev-tester）の数と subagent の回数 — 報告書の「Checker」行の出所（無ければ「記録なし」）
    ck = st.get("checker") if isinstance(st.get("checker"), dict) else {}
    for key, v in (("findings", a.checker_findings), ("remaining", a.checker_remaining),
                   ("reviewer_rounds", a.reviewer_rounds), ("tester_rounds", a.tester_rounds)):
        if v is not None:
            if v < 0:
                out("⛔ --%s は 0 以上（exit 2）" % key.replace("_", "-"))
                return 2
            ck[key] = v
    if ck:
        st["checker"] = ck
    if a.subagents is not None:
        if a.subagents < 0:
            out("⛔ --subagents は 0 以上（exit 2）")
            return 2
        st["subagents"] = a.subagents
    if a.status:
        if a.status not in NOTE_STATUSES:
            out("⛔ note で書ける status は %s だけ — running に戻すのは resume（人）、stopped は人が置く STOP（boundary）（exit 2）"
                % " / ".join(NOTE_STATUSES))
            return 2
        if a.status == "delivered":
            why = []
            hist = st.get("history") or []
            last = hist[-1] if hist else None
            if not last or last.get("green") != last.get("total") or last.get("violations"):
                why.append("直近の round が全部緑・違反 0 でない")
            mut = st.get("mutation") or []
            if not mut or any(m.get("result") != "red" for m in mut):
                why.append("mutation が全部赤でない（未実施を含む）")
            if not st.get("pr"):
                why.append("PR が無い（--pr）")
            if not st.get("not_done"):
                why.append("やらなかったことが 0 件（空なら「見ていない」と同じ・--not-done）")
            pc = protected_changes(root, st)
            if pc:
                why.append("契約・gate 設定が変わった: " + ", ".join(pc))
            vt = st.get("verified_tree")
            try:
                now_tree = tree_of(root)
            except RuntimeError:
                now_tree = None
            head_tree = git(root, "rev-parse", "HEAD^{tree}").stdout.strip()
            if not vt or now_tree != vt:
                why.append("round で緑になった後に作業ツリーが変わった（round をやり直す）")
            elif head_tree != vt:
                why.append("PR に載る commit（HEAD）が round で緑になった木と違う — 検証した中身を全部 commit し、それ以外を commit しない")
            if why:
                out("⛔ delivered にできない: " + " / ".join(why) + "（exit 1）")
                return 1
            st["phase"] = "deliver"
        if a.status == "escalated":
            if not str(st.get("blocker") or "").strip():
                out("⛔ escalated には blocker（人が要る理由 1 行）が要る — --blocker（exit 1）")
                return 1
            st["phase"] = "escalate"
        st["status"] = a.status
        if a.status in ("delivered", "escalated", "stopped"):
            st["finished_at"] = O.now_iso()   # 報告書の「経過」を終わった時点で止める
    O.save_state(root, st)
    out("✅ state を更新（status=%s・phase=%s・やらなかったこと %d 件・所見 %d 件）"
        % (st.get("status"), st.get("phase"), len(st.get("not_done") or []), len(st.get("findings") or [])))
    return 0


def do_status(root):
    st = state_or_die(root)
    dod = st.get("dod") or []
    el, _ = fmt_elapsed(st)
    out("oneshot: status=%s・phase=%s・周 %s / 上限 %s・dod %d/%d green・%s・%s%s"
        % (st.get("status"), st.get("phase"), st.get("round"), (st.get("budget") or {}).get("attempts"),
           sum(1 for e in dod if e.get("result") == "green"), len(dod), el, fmt_tokens(st),
           ("・blocker: " + st["blocker"]) if st.get("blocker") else ""))
    return 0


# ─────────────────────────── self-test ───────────────────────────

CALC = "def add(a, b):\n    return a + b\n"
CHECK_NEW = ("import sys\nsys.path.insert(0, 'src')\n"
             "try:\n    from calc import mul\nexcept ImportError:\n    print('Tests: 0 passed'); sys.exit(1)\n"
             "ok = mul(3, 4) == 12\nprint('Tests: 1 passed' if ok else 'Tests: 0 passed')\nsys.exit(0 if ok else 1)\n")
CHECK_OLD = "import sys\nsys.path.insert(0, 'src')\nfrom calc import add\nsys.exit(0 if add(2, 3) == 5 else 1)\n"
MUT_OK = "python3 -c \"p='src/calc.py';s=open(p).read();open(p,'w').write(s.replace('a * b','a + b'))\""
MUT_NOEFF = "python3 -c \"p='src/calc.py';s=open(p).read();open(p,'w').write(s.replace('a ** b','a + b'))\""
CONTRACT_T = """goal: "calc に mul を足す"
dod:
  - kind: new
    cmd: "python3 -B tests/check_new.py"
    count: 'Tests:\\s+(\\d+) passed'
    mutate: "{mut}"
  - kind: regression
    cmd: "python3 -B tests/check_old.py"
  - kind: boundary
    cmd: "test ! -e secret.env"
scope:
  paths: ["src/", "tests/"]
forbid:
  paths: ["infra/"]
  gate_config: ["floor.json"]
budget:
  attempts: 50
  minutes: {minutes}
  tokens: 100000
level: {level}
"""


def _self_test():
    ok = ng = 0

    def check(name, cond, detail=""):
        nonlocal ok, ng
        if cond:
            ok += 1
            print("  ✅ " + name)
        else:
            ng += 1
            print("  ❌ " + name + (("  — " + str(detail)[:400]) if detail else ""))

    me = os.path.abspath(__file__)

    def run(root, *args):
        env = {**os.environ, "PH_TARGET_ROOT": root, "ONESHOT_CMD_TIMEOUT": "60"}
        env.pop("CLAUDECODE", None)   # self-test は人の端末を模す（Claude Code の中で回しても同じ結果にする）
        r = subprocess.run([sys.executable, me, *args], cwd=root, capture_output=True, text=True, env=env)
        return r.returncode, r.stdout + r.stderr

    def w(root, rel, text):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p) or root, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)

    def sh(root, *cmd):
        return subprocess.run(list(cmd), cwd=root, capture_output=True, text=True)

    def approve(d):
        """人が spec.md を見て「走らせてよい」と言い、契約と spec を commit した状態を作る。"""
        sha = (O.sha256_file(os.path.join(d, "oneshot.yaml")) or "")[:16]
        w(d, "oneshot/spec.md", "# spec\n> 元は `oneshot.yaml`（sha256 `%s`）。\n" % sha)
        sh(d, "git", "add", "oneshot.yaml", "oneshot/spec.md")
        sh(d, "git", "commit", "-q", "-m", "contract", "--", "oneshot.yaml", "oneshot/spec.md")

    def commit_all(d, msg="wip"):
        sh(d, "git", "add", "-A")
        sh(d, "git", "commit", "-q", "-m", msg)

    def mkrepo(d, mut=MUT_OK, minutes=60, level=1):
        os.makedirs(d, exist_ok=True)
        sh(d, "git", "init", "-q", "-b", "main")
        sh(d, "git", "config", "user.email", "oneshot-selftest")
        sh(d, "git", "config", "user.name", "t")
        w(d, "src/calc.py", CALC)
        w(d, "tests/check_new.py", CHECK_NEW)
        w(d, "tests/check_old.py", CHECK_OLD)
        w(d, "floor.json", '{"floor": 80}\n')
        w(d, "README.md", "x\n")
        sh(d, "git", "add", "-A")
        sh(d, "git", "commit", "-q", "-m", "init")
        w(d, "oneshot.yaml", CONTRACT_T.format(mut=mut.replace('"', '\\"'), minutes=minutes, level=level))
        return d

    print("── oneshot-preflight self-test ──")
    with tempfile.TemporaryDirectory() as tmp:
        R = mkrepo(os.path.join(tmp, "r"))

        # ── check ──
        rc, o = run(R, "check")
        check("check pass : 正しい契約 → exit 0", rc == 0, o)
        bad = open(os.path.join(R, "oneshot.yaml")).read()
        w(R, "oneshot.bad.yaml", bad.replace("  - kind: boundary\n    cmd: \"test ! -e secret.env\"\n", ""))
        rc, o = run(R, "check", "--contract", os.path.join(R, "oneshot.bad.yaml"))
        check("check block: 境界の dod が無い → exit 1（Phase −1 へ戻す）", rc == 1 and "boundary" in o, o)
        w(R, "oneshot.bad.yaml", bad.replace("level: 1", "level: 2"))
        rc, o = run(R, "check", "--contract", os.path.join(R, "oneshot.bad.yaml"))
        check("check block: level 2 → exit 2（拒否）", rc == 2, o)
        w(R, "oneshot.bad.yaml", "goal: [閉じていない\n")
        rc, o = run(R, "check", "--contract", os.path.join(R, "oneshot.bad.yaml"))
        check("check block: 読めない契約 → exit 2（❓）", rc == 2 and "❓" in o, o)
        os.unlink(os.path.join(R, "oneshot.bad.yaml"))

        # ── start ──
        rc, o = run(R, "start")
        check("start block: 既定ブランチ（main）の上 → exit 2", rc == 2 and "既定ブランチ" in o, o)
        sh(R, "git", "checkout", "-q", "-b", "feat/mul")
        rc, o = run(R, "start")
        check("start block: spec.md が無い → exit 1", rc == 1 and "spec.md" in o, o)
        w(R, "oneshot/spec.md", "# spec\n")
        rc, o = run(R, "start")
        check("start block: spec.md に契約の sha256 が無い → exit 1", rc == 1 and "sha256" in o, o)
        sha = (O.sha256_file(os.path.join(R, "oneshot.yaml")) or "")[:16]
        w(R, "oneshot/spec.md", "# spec\n> 元は `oneshot.yaml`（sha256 `%s`）。\n" % sha)
        rc, o = run(R, "start")
        check("start block: 契約と spec が commit されていない → exit 1", rc == 1 and "commit" in o, o)
        approve(R)
        ctext0 = open(os.path.join(R, "oneshot.yaml")).read()
        w(R, "oneshot.yaml", ctext0.replace("minutes: 60", "minutes: 61"))
        rc, o = run(R, "start")
        check("start block: 承認後に契約が変わった（spec の sha256 と違う）→ exit 1", rc == 1 and "承認" in o, o)
        w(R, "oneshot.yaml", ctext0)
        # red-first: 実装が先に入っている（全部緑）→ 拒否
        w(R, "src/calc.py", CALC + "\n\ndef mul(a, b):\n    return a * b\n")
        rc, o = run(R, "start")
        check("start block: 作業ツリーが clean でない → exit 1（commit 済みの基準点から始める）", rc == 1 and "clean" in o, o)
        commit_all(R, "impl-first")
        rc, o = run(R, "start")
        check("start block: red-first — 実装前から全部緑 → exit 1・state を作らない",
              rc == 1 and "全部緑" in o and not os.path.exists(os.path.join(R, "oneshot", "state.json")), o)
        # 新規の判定だけが実装前から緑（回帰は赤）→ この変更を測れていない
        w(R, "src/calc.py", "def add(a, b):\n    return 0\n\n\ndef mul(a, b):\n    return a * b\n")
        commit_all(R, "new-only-green")
        rc, o = run(R, "start")
        check("start block: 新規の判定が実装前から緑（他は赤）→ exit 1・state を作らない",
              rc == 1 and "新規の判定が実装前から緑" in o and not os.path.exists(os.path.join(R, "oneshot", "state.json")), o)
        # 代替: 実装を戻し、新規の判定が赤の状態から始める
        w(R, "src/calc.py", CALC)
        commit_all(R, "red-start")
        rc, o = run(R, "start")
        st = O.load_state(R) or {}
        check("start 代替: 新規の判定が赤の状態から → exit 0・status=running", rc == 0 and st.get("status") == "running", o)
        check("start pass : state に hook が読む項目（status / started_at / budget.minutes / blocker / dod[].result）",
              all(k in st for k in ("status", "started_at", "budget", "blocker", "dod"))
              and st["budget"].get("minutes") == 60 and all("result" in e for e in st["dod"]), st)
        check("start pass : red-first を記録（dod 3 本中 1 本 red）", st.get("red_first", {}).get("red") == 1
              and st["red_first"]["total"] == 3, st.get("red_first"))
        check("start pass : protected に契約・spec・gate 設定のハッシュ",
              set(st.get("protected", {})) == {"oneshot.yaml", "oneshot/spec.md", "floor.json"}, st.get("protected"))
        check("start pass : state.json は git から無視される（info/exclude）",
              sh(R, "git", "check-ignore", "-q", "oneshot/state.json").returncode == 0)
        rc, o = run(R, "start")
        check("start block: status=running の state がある → exit 2（並列非対応）", rc == 2, o)

        # hook との整合（在れば）
        hooks = [os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(me))), "core", "hooks"),
                 os.path.join(os.path.dirname(os.path.dirname(me)), "hooks")]
        hk = next((os.path.join(h, "stop-oneshot-continue.sh") for h in hooks
                   if os.path.isfile(os.path.join(h, "stop-oneshot-continue.sh"))), None)
        if hk:
            r = subprocess.run(["bash", hk], input="{}", capture_output=True, text=True,
                               env={**os.environ, "CLAUDE_PROJECT_DIR": R, "ONESHOT_STATE": ""})
            check("hook 整合: start 直後の state を Stop hook が読み、red の dod を名指しして block",
                  '"decision": "block"' in r.stdout and "check_new.py" in r.stdout, r.stdout)
            try:
                os.unlink(os.path.join(R, "oneshot", ".hooks.json"))
            except OSError:
                pass
        else:
            print("  ℹ️  stop-oneshot-continue.sh が見つからない — hook との整合は skip")

        # ── round ──
        rc, o = run(R, "round")
        check("round block: 新規の判定が赤 → exit 1", rc == 1 and "dod[0]" in o, o)
        w(R, "src/calc.py", CALC + "\n\ndef mul(a, b):\n    return a * b\n")
        rc, o = run(R, "round")
        check("round pass : 実装して全部緑・違反 0 → exit 0", rc == 0, o)
        st = O.load_state(R) or {}
        check("round pass : round・history・last_diff を書く", st.get("round") == 2 and len(st.get("history", [])) == 2
              and "file" in st.get("last_diff", ""), st)
        check("round pass : 進捗 1 行が分母つき（dod N/M green）", "dod 3/3 green" in o, o)
        w(R, "docs/notes.md", "scope の外\n")
        rc, o = run(R, "round")
        check("round block: scope 外への変更（未追跡）→ exit 1", rc == 1 and "scope 外への変更: docs/notes.md" in o, o)
        os.unlink(os.path.join(R, "docs", "notes.md"))
        os.rmdir(os.path.join(R, "docs"))
        rc, o = run(R, "round")
        check("round 代替: scope 外の変更を戻すと通る → exit 0", rc == 0, o)
        w(R, "floor.json", '{"floor": 0}\n')
        rc, o = run(R, "round")
        check("round block: gate 設定（floor）を緩める → exit 1", rc == 1 and "floor.json" in o, o)
        sh(R, "git", "checkout", "-q", "--", "floor.json")
        w(R, "infra/main.tf", "x\n")
        rc, o = run(R, "round")
        check("round block: forbid（infra/）への変更 → exit 1", rc == 1 and "forbid への変更: infra/main.tf" in o, o)
        os.unlink(os.path.join(R, "infra", "main.tf"))
        os.rmdir(os.path.join(R, "infra"))
        ctext = open(os.path.join(R, "oneshot.yaml")).read()
        w(R, "oneshot.yaml", ctext.replace("minutes: 60", "minutes: 600"))
        rc, o = run(R, "round")
        check("round block: 契約（oneshot.yaml）を書き換える → exit 1", rc == 1 and "oneshot.yaml" in o, o)
        w(R, "oneshot.yaml", ctext)
        rc, o = run(R, "round")
        check("round 代替: 契約を元に戻すと通る → exit 0", rc == 0, o)
        # rename: `git mv` で scope 外（README）を scope 内へ移しても、元の場所の削除を数える
        sh(R, "git", "mv", "README.md", "src/README.md")
        rc, o = run(R, "round")
        check("round block: git mv で scope 外を scope 内へ移す → exit 1（削除を数える）",
              rc == 1 and "scope 外への変更: README.md" in o, o)
        sh(R, "git", "mv", "src/README.md", "README.md")
        rc, o = run(R, "round")
        check("round 代替: 移動を戻すと通る → exit 0", rc == 0, o)
        # HEAD も見る: scope 外を commit してから作業ツリーだけ戻しても、PR には載るので違反
        base = (O.load_state(R) or {}).get("base_ref")
        w(R, "README.md", "changed\n")
        sh(R, "git", "commit", "-q", "-m", "wip-outside", "--", "README.md")
        sh(R, "git", "checkout", "-q", base, "--", "README.md")
        rc, o = run(R, "round")
        check("round block: scope 外を commit → 作業ツリーだけ戻す → exit 1（HEAD に残る）",
              rc == 1 and "scope 外への変更: README.md" in o, o)
        sh(R, "git", "reset", "-q", "--mixed", base)
        rc, o = run(R, "round")
        check("round 代替: commit ごと戻すと通る → exit 0", rc == 0, o)
        # count: 0 件・取れない は赤
        w(R, "tests/check_new.py", "print('no tests ran')\n")
        rc, o = run(R, "round")
        check("round block: exit 0 でも実行件数が取れない → 赤（0 件と同じ）", rc == 1 and "実行件数が取れない" in o, o)
        w(R, "tests/check_new.py", "print('Tests: 0 passed')\n")
        rc, o = run(R, "round")
        check("round block: 実行件数 0 件 → 赤", rc == 1 and "0 件" in o, o)
        w(R, "tests/check_new.py", CHECK_NEW)
        rc, o = run(R, "round")
        check("round 代替: 判定を戻すと通る → exit 0", rc == 0, o)

        # ── mutate ──
        w(R, "src/extra.py", "x = 1\n")
        rc, o = run(R, "mutate")
        check("mutate block: round で緑になった後に木が変わった → exit 1（round をやり直す）", rc == 1 and "round をやり直す" in o, o)
        os.unlink(os.path.join(R, "src", "extra.py"))
        before_status = sh(R, "git", "status", "--porcelain").stdout
        before_cached = sh(R, "git", "diff", "--cached").stdout
        before_calc = open(os.path.join(R, "src", "calc.py")).read()
        rc, o = run(R, "mutate")
        st = O.load_state(R) or {}
        check("mutate pass : 壊すと赤 → exit 0", rc == 0 and [m["result"] for m in st.get("mutation", [])] == ["red"], o)
        check("mutate pass : 作業ツリーを元に戻す（中身・status とも一致）",
              open(os.path.join(R, "src", "calc.py")).read() == before_calc
              and sh(R, "git", "status", "--porcelain").stdout == before_status,
              sh(R, "git", "status", "--porcelain").stdout)
        check("mutate pass : 本物の index に触らない", sh(R, "git", "diff", "--cached").stdout == before_cached)

        # ── boundary ──
        rc, o = run(R, "boundary")
        check("boundary pass: 予算内 → exit 0", rc == 0, o)
        st = O.load_state(R)
        att0 = st["budget"]["attempts"]
        st["budget"]["attempts"] = int(st.get("round") or 0)
        O.save_state(R, st)
        rc, o = run(R, "boundary")
        check("boundary block: 試行の上限に達した → exit 2（goal-loop の state に依らない）", rc == 2 and "試行の上限" in o, o)
        st = O.load_state(R)
        st["budget"]["attempts"] = att0
        O.save_state(R, st)
        check("boundary pass: transcript が無ければトークンは ❓（0 と書かない）", "❓" in o, o)
        # トークン: transcript + subagent・started_at 前は数えない・message.id の重複は 1 回
        tdir = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(tdir, "sess", "subagents"))
        tp = os.path.join(tdir, "sess.jsonl")
        st = O.load_state(R) or {}
        t0 = O.parse_iso(st["started_at"])
        import datetime as _dt

        def iso(t):
            return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat().replace("+00:00", "Z")

        def am(mid, t, i, cc, o_):
            return json.dumps({"type": "assistant", "timestamp": iso(t), "message": {"id": mid, "usage": {
                "input_tokens": i, "cache_creation_input_tokens": cc, "cache_read_input_tokens": 999999,
                "output_tokens": o_}}}) + "\n"

        with open(tp, "w") as fh:
            fh.write(am("m0", t0 - 100, 50000, 0, 0))            # 開始前は数えない
            fh.write(am("m1", t0 + 1, 100, 1000, 10))            # 同じ id が 2 行（1 回だけ）
            fh.write(am("m1", t0 + 1, 100, 1000, 20))
            fh.write('{"type":"user"}\n')
        with open(os.path.join(tdir, "sess", "subagents", "agent-x.jsonl"), "w") as fh:
            fh.write(am("s1", t0 + 2, 5, 500, 5))
        w(R, "oneshot/.hooks.json", json.dumps({"transcript_path": tp}))
        rc, o = run(R, "boundary")
        st = O.load_state(R) or {}
        check("tokens: 本体 + subagent・開始前は除く・id の重複は 1 回（1120 + 510 = 1630）",
              (st.get("tokens") or {}).get("used") == 1630, st.get("tokens"))
        with open(tp, "a") as fh:
            fh.write(am("m2", t0 + 3, 0, 200000, 0))
        rc, o = run(R, "boundary")
        check("boundary block: トークンの予算超過 → exit 2（CAP）", rc == 2 and "トークンの予算" in o, o)
        check("boundary block: CAP は §3-7 escalate の手順を案内する", "note --status escalated" in o, o)
        os.unlink(os.path.join(R, "oneshot", ".hooks.json"))
        st = O.load_state(R)
        st["started_at"] = iso(time.time() - 7200)
        O.save_state(R, st)
        rc, o = run(R, "boundary")
        check("boundary block: 時間の予算超過 → exit 2（CAP）", rc == 2 and "時間の予算" in o, o)
        st["started_at"] = O.now_iso()
        O.save_state(R, st)
        w(R, "oneshot/STOP", "")
        rc, o = run(R, "boundary")
        st2 = O.load_state(R) or {}
        check("boundary block: oneshot/STOP → exit 3・status=stopped・interrupted を記録",
              rc == 3 and st2.get("status") == "stopped" and st2.get("interrupted"), o)
        os.unlink(os.path.join(R, "oneshot", "STOP"))
        st["status"] = "running"
        O.save_state(R, st)

        # ── note ──
        rc, o = run(R, "note", "--phase", "nope")
        check("note block: 知らない phase → exit 2", rc == 2, o)
        rc, o = run(R, "note", "--status", "delivered", "--pr", "https://example.invalid/pr/1")
        check("note block: やらなかったこと 0 件で delivered → exit 1", rc == 1 and "やらなかったこと" in o, o)
        check("note block: 失敗した note は何も保存しない（pr が残らない）", not (O.load_state(R) or {}).get("pr"))
        rc, o = run(R, "note", "--status", "running")
        check("note block: running に戻す → exit 2（resume は人だけ）", rc == 2, o)
        rc, o = run(R, "note", "--status", "stopped")
        check("note block: stopped にする → exit 2（stopped は人が置く STOP だけ）", rc == 2, o)
        rc, o = run(R, "note", "--status", "delivered", "--pr", "https://example.invalid/pr/1",
                    "--not-done", "README の更新（scope 外）")
        check("note block: 検証した中身を commit していない → exit 1（PR に載る HEAD と違う）",
              rc == 1 and "HEAD" in o, o)
        commit_all(R, "impl")
        rc, o = run(R, "note", "--status", "delivered", "--pr", "https://example.invalid/pr/1",
                    "--not-done", "README の更新（scope 外）")
        check("note 代替: 中身を commit し、やらなかったことを書くと delivered → exit 0",
              rc == 0 and (O.load_state(R) or {}).get("status") == "delivered", o)
        st = O.load_state(R)
        st["status"] = "running"
        O.save_state(R, st)
        rc, o = run(R, "note", "--status", "escalated")
        check("note block: blocker なしで escalated → exit 1", rc == 1, o)
        rc, o = run(R, "note", "--status", "escalated", "--blocker", "API キーの発行が要る（人）")
        check("note 代替: blocker を書くと escalated → exit 0", rc == 0 and (O.load_state(R) or {}).get("status") == "escalated", o)
        rc, o = run(R, "status")
        check("status pass: 1 行の進捗（dod N/M green）", rc == 0 and "dod 3/3 green" in o and "blocker" in o, o)

        # ── mutate の block / 代替（別のリポ）──
        R2 = mkrepo(os.path.join(tmp, "r2"), mut=MUT_NOEFF)
        sh(R2, "git", "checkout", "-q", "-b", "feat/mul")
        approve(R2)
        rc, o = run(R2, "start")
        check("mutate 準備: start → exit 0", rc == 0, o)
        rc, o = run(R2, "mutate")
        check("mutate block: 直近の round が緑でない → exit 1", rc == 1 and "round を先に" in o, o)
        w(R2, "src/calc.py", CALC + "\n\ndef mul(a, b):\n    return a * b\n")
        rc, o = run(R2, "round")
        rc, o = run(R2, "mutate")
        st = O.load_state(R2) or {}
        check("mutate block: mutate が何も変えない → exit 1・no-effect を所見に",
              rc == 1 and [m["result"] for m in st.get("mutation", [])] == ["no-effect"]
              and any("何も変えなかった" in f for f in st.get("findings", [])), o)
        ctext = open(os.path.join(R2, "oneshot.yaml")).read()
        w(R2, "oneshot.yaml", ctext.replace("a ** b", "a * b"))
        rc, o = run(R2, "mutate")
        check("mutate block: 無人区間で契約の mutate を書き換えて当てる → exit 1（抜け道を塞ぐ）", rc == 1 and "契約" in o, o)
        rc, o = run(R2, "resume")
        check("resume block: status=running のまま resume → exit 2（人がループに戻った後だけ）", rc == 2, o)
        r = subprocess.run([sys.executable, me, "resume"], cwd=R2, capture_output=True, text=True,
                           env={**os.environ, "PH_TARGET_ROOT": R2, "CLAUDECODE": "1"})
        check("resume block: Claude Code の中（CLAUDECODE=1）からの resume → exit 2（hook を外しても道具が止める）",
              r.returncode == 2 and "人だけ" in r.stdout, r.stdout)
        r = subprocess.run([sys.executable, me, "--ro", R2, "status"], cwd=R2, capture_output=True, text=True,
                           env={**os.environ, "PH_TARGET_ROOT": R2})
        check("argparse: 省略形（--ro）は受けない → exit 2（hook の検出を外す書き方を塞ぐ）", r.returncode == 2, r.stderr[-200:])

        def human_fix(new_text):
            run(R2, "note", "--status", "escalated", "--blocker", "測定器: mutate を直す（人）")
            w(R2, "oneshot.yaml", new_text)
            approve(R2)
            rc_, o_ = run(R2, "resume")
            run(R2, "round")
            return rc_, o_

        run(R2, "note", "--status", "escalated", "--blocker", "測定器: mutate を直す（人）")
        w(R2, "oneshot.yaml", ctext.replace("a ** b", "a * b"))
        rc, o = run(R2, "resume")
        check("resume block: 人が直した契約が commit / 承認されていない → exit 1", rc == 1, o)
        w(R2, "oneshot.yaml", ctext)
        rc, o = human_fix(ctext.replace("a ** b", "a * b"))
        check("resume 代替: escalate → 人が契約を直す → resume → exit 0・status=running",
              rc == 0 and (O.load_state(R2) or {}).get("status") == "running", o)
        rc, o = run(R2, "mutate")
        check("mutate 代替: mutate を当たる形に直すと赤 → exit 0", rc == 0, o)
        human_fix(ctext.replace(MUT_NOEFF.replace('"', '\\"'), "python3 -c \\\"open('README.md','a').write('y')\\\""))
        rc, o = run(R2, "mutate")
        st = O.load_state(R2) or {}
        check("mutate block: 壊しても緑（判定が見ていない所を壊す）→ exit 1・測定器の所見",
              rc == 1 and [m["result"] for m in st.get("mutation", [])] == ["green"]
              and any("測定器が壊れている" in f for f in st.get("findings", [])), o)
        check("mutate pass : 緑のケースでも作業ツリーを戻す（README は元のまま）",
              open(os.path.join(R2, "README.md")).read() == "x\n")
        human_fix(ctext.replace(MUT_NOEFF.replace('"', '\\"'), "python3 -c \\\"open('src/new_file.py','w').write('x')\\\" && false"))
        rc, o = run(R2, "mutate")
        st = O.load_state(R2) or {}
        check("mutate block: mutate 自体が失敗 → exit 1・error・新規ファイルも消して戻す",
              rc == 1 and [m["result"] for m in st.get("mutation", [])] == ["error"]
              and not os.path.exists(os.path.join(R2, "src", "new_file.py")), o)

        # ── resume は名指しした scope 外の変更だけを承認する ──
        run(R2, "note", "--status", "escalated", "--blocker", "生成物の ignore が要る（人）")
        w(R2, "notes.txt", "人が足した\n")
        w(R2, "other.txt", "誰が足したか分からない\n")
        rc, o = run(R2, "resume", "--accept", "nope.txt")
        check("resume block: 名指しした変更が見当たらない → exit 1", rc == 1 and "nope.txt" in o, o)
        rc, o = run(R2, "resume", "--accept", "notes.txt")
        check("resume pass : 名指しした notes.txt だけ承認し、other.txt は違反のまま残すと出す",
              rc == 0 and "notes.txt" in o and "other.txt" in o, o)
        rc, o = run(R2, "round")
        check("round block: 承認していない scope 外（other.txt）は違反", rc == 1 and "other.txt" in o and "notes.txt" not in o.split("scope 外への変更")[-1], o)
        os.unlink(os.path.join(R2, "other.txt"))
        rc, o = run(R2, "round")
        check("round 代替: 承認していないものを戻すと通る（notes.txt は承認済み）→ exit 0", rc == 0, o)

        # ── 止まった run の上で start し直さない（自己再開）・退避は検査の後 ──
        run(R2, "note", "--status", "escalated", "--blocker", "人の判断待ち")
        before = open(os.path.join(R2, "oneshot", "state.json")).read()
        rc, o = run(R2, "start")
        check("start block: escalated の state の上で start → exit 2（周・予算・基準点を初期化させない）",
              rc == 2 and "resume" in o, o)
        check("start block: 拒否しても state.json はそのまま（hook も resume も効き続ける）",
              open(os.path.join(R2, "oneshot", "state.json")).read() == before)
        st = O.load_state(R2)
        st["status"] = "delivered"
        O.save_state(R2, st)
        commit_all(R2, "impl-done")   # 実装が入った状態 = new が実装前から緑 → red-first で落ちる
        rc, o = run(R2, "start")
        check("start block: 前回 delivered でも red-first で落ちたら state.json を退避しない（消さない）",
              rc == 1 and (O.load_state(R2) or {}).get("status") == "delivered", o)

        # ── gate_config にディレクトリを書いても、触らなければ毎周の違反にならない ──
        R4 = mkrepo(os.path.join(tmp, "r4"))
        w(R4, "cfg/floor.json", '{"floor": 80}\n')
        ct = open(os.path.join(R4, "oneshot.yaml")).read().replace('gate_config: ["floor.json"]', 'gate_config: ["cfg/"]')
        w(R4, "oneshot.yaml", ct)
        sh(R4, "git", "add", "cfg/floor.json")
        sh(R4, "git", "commit", "-q", "-m", "cfg", "--", "cfg/floor.json")
        sh(R4, "git", "checkout", "-q", "-b", "feat/mul")
        approve(R4)
        rc, o = run(R4, "start")
        check("gate dir: gate_config がディレクトリでも start → exit 0", rc == 0, o)
        w(R4, "src/calc.py", CALC + "\n\ndef mul(a, b):\n    return a * b\n")
        rc, o = run(R4, "round")
        check("gate dir: cfg/ に触らなければ違反 0 → exit 0（毎周「変わった」にならない）", rc == 0, o)
        w(R4, "cfg/floor.json", '{"floor": 0}\n')
        rc, o = run(R4, "round")
        check("gate dir: cfg/ の中を緩めると gate 設定への変更 → exit 1", rc == 1 and "gate 設定への変更: cfg/floor.json" in o, o)

        # ── worktree でも exclude が効く ──
        R3 = mkrepo(os.path.join(tmp, "r3"))
        sh(R3, "git", "add", "-A")
        sh(R3, "git", "commit", "-q", "-m", "contract")
        WT = os.path.join(tmp, "wt")
        sh(R3, "git", "worktree", "add", "-q", "-b", "feat/wt", WT)
        approve(WT)
        rc, o = run(WT, "start")
        check("start pass : worktree でも state を作り、state.json が git から無視される",
              rc == 0 and sh(WT, "git", "check-ignore", "-q", "oneshot/state.json").returncode == 0, o)
        rc, o = run(os.path.join(tmp, "r"), "status")
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        sh(empty, "git", "init", "-q")
        rc, o = run(empty, "status")
        check("status block: state が無い → exit 2（❓）", rc == 2 and "❓" in o, o)

    print("\n検査 %d 件: 合格 %d / 不合格 %d" % (ok + ng, ok, ng))
    if ng:
        return 1
    print("✅ PASS — 契約・red-first・毎周の判定・mutation・予算・割り込みを exit code で決める")
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return _self_test()
    ap = argparse.ArgumentParser(prog="oneshot-preflight.py", description=__doc__.splitlines()[0], allow_abbrev=False)
    ap.add_argument("--root", help="対象リポ（既定: PH_TARGET_ROOT / git toplevel）")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("check")
    p.add_argument("--contract")
    for n in ("start", "round", "boundary", "mutate", "status"):
        sub.add_parser(n)
    p = sub.add_parser("resume")
    p.add_argument("--accept", action="append", help="人が承認する scope 外の変更（名指し・繰り返し可）")
    p = sub.add_parser("note")
    p.add_argument("--phase")
    p.add_argument("--text")
    p.add_argument("--not-done", action="append")
    p.add_argument("--finding", action="append")
    p.add_argument("--blocker")
    p.add_argument("--status")
    p.add_argument("--pr")
    p.add_argument("--checker-findings", type=int, help="Checker の指摘の総数")
    p.add_argument("--checker-remaining", type=int, help="残っている指摘の数")
    p.add_argument("--reviewer-rounds", type=int, help="dev-reviewer を回した周の数")
    p.add_argument("--tester-rounds", type=int, help="dev-tester を回した周の数")
    p.add_argument("--subagents", type=int, help="subagent を起動した回数（合計）")
    a = ap.parse_args()
    if not a.cmd:
        ap.print_help()
        return 2
    root = os.path.abspath(a.root) if a.root else O.repo_root()
    if a.cmd == "check":
        return do_check(root, a.contract)[0]
    if a.cmd == "resume":
        return do_resume(root, a.accept)
    return {"start": do_start, "round": do_round, "boundary": do_boundary, "mutate": do_mutate,
            "status": do_status, "resume": do_resume}.get(a.cmd, lambda r: do_note(r, a))(root)


if __name__ == "__main__":
    sys.exit(main())
