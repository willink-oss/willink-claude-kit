#!/usr/bin/env python3
"""oneshot-report.py — /oneshot の報告書（分母つき・PR 本文に貼る）を state.json と git から作る。

なぜ在るか（2026-09-25・docs/design/oneshot-mode.md §5・§8-5）:
  無人区間の結果を人が読むのは PR の本文だけ。そこに「できました」と書くのでは自己申告になる。
  報告書は **state.json（周ごとの機械の記録）と git の差分だけ** から作り、すべての件数に分母を付ける。
  「やらなかったこと」が空の報告は「見ていない」と同じなので通さない（exit 1）。

何を出すか（§5 の型）:
  - dod の緑 / 全体・試行 / 上限・経過 / 予算・トークン / 予算（取れなければ ❓）
  - 変更: ファイル数 / 追加 −削除（scope 内 / scope 外）。oneshot.yaml と oneshot/ は数えない（契約と成果物）
  - Checker（state に記録が無ければ「記録なし」）
  - red-first・mutation-first・new の実行件数
  - forbid への変更 N 件（走査 M パス）・割り込み
  - やらなかったこと（必須）・測定器の所見（findings と mutation の green / no-effect / error から）

usage:
  oneshot-report.py [--root R] [--json]
  oneshot-report.py --self-test
exit: 0 = 報告書を出した / 1 = 「やらなかったこと」が空（見ていない）/ 2 = state が無い・読めない
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _oneshot as O  # noqa: E402

BUILD_PHASE = {"explore": 1, "plan": 2, "implement": 3, "verify": 4, "loop": 4, "mutate": 4}


def _git(root, args):
    try:
        r = subprocess.run(["git"] + args, cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _is_artifact(rel):
    return rel == O.CONTRACT or rel.startswith(O.ODIR + "/")


def diff_stats(root, base, contract):
    """{"files","add","del","in_scope","out_scope","out_list","untracked"} / 取れなければ None。"""
    if not base:
        return None
    num = _git(root, ["diff", "--no-renames", "--numstat", base])
    if num is None:
        return None
    files = {}
    for line in num.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d, rel = parts[0], parts[1], parts[-1]
        if " => " in rel:  # rename の表記は新しい名前で数える
            rel = rel.split(" => ")[-1].replace("}", "").replace("{", "")
        if _is_artifact(rel):
            continue
        files[rel] = (int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0)
    untracked = 0
    others = _git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    for rel in (others or "").split("\0"):
        if not rel or _is_artifact(rel) or rel in files:
            continue
        try:
            with open(os.path.join(root, rel), "rb") as fh:
                n = fh.read().count(b"\n")
        except OSError:
            n = 0
        files[rel] = (n, 0)
        untracked += 1
    ins = out = 0
    out_list = []
    for rel in sorted(files):
        if contract is not None and O.classify_path(contract, rel) == "writable":
            ins += 1
        else:
            out += 1
            out_list.append(rel)
    return {"files": len(files), "add": sum(v[0] for v in files.values()), "del": sum(v[1] for v in files.values()),
            "in_scope": ins if contract is not None else None, "out_scope": out if contract is not None else None,
            "out_list": out_list if contract is not None else [], "untracked": untracked}


def _k(n):
    if n is None:
        return "❓"
    return ("%dk" % round(n / 1000.0)) if n >= 1000 else str(n)


def _elapsed_s(st):
    t0 = O.parse_iso(st.get("started_at"))
    if t0 is None:
        return None
    end = O.parse_iso(st.get("finished_at"))
    if end is None and str(st.get("status") or "") in O.TERMINAL:
        hist = st.get("history") or []
        if hist and isinstance(hist[-1], dict):
            end = O.parse_iso(hist[-1].get("at"))
    return int((end if end is not None else time.time()) - t0)


def build(root):
    st = O.load_state(root)
    if st is None:
        return None, "state.json が無い・読めない: %s" % O.state_path(root)
    contract, _err = O.load_contract(root)
    dod = [e for e in (st.get("dod") or []) if isinstance(e, dict)]
    b = st.get("budget") or {}
    green = sum(1 for e in dod if e.get("result") == "green")
    news = [e for e in dod if e.get("kind") == "new"]
    el = _elapsed_s(st)
    tok = st.get("tokens") or {}
    used = tok.get("used") if isinstance(tok, dict) else None

    head = "dod: %d 本中 %d 本 green（試行 %s / 上限 %s・経過 %s 分 / %s 分・トークン %s / %s%s%s）" % (
        len(dod), green, st.get("round", "❓"), b.get("attempts", "❓"),
        (el // 60) if el is not None else "❓", b.get("minutes", "❓"),
        _k(used), _k(b.get("tokens")) if isinstance(b.get("tokens"), int) else "❓",
        ("（%s）" % tok.get("how")) if used is None and isinstance(tok, dict) and tok.get("how") else "",
        ("・subagent %d 回" % st["subagents"]) if isinstance(st.get("subagents"), int) else "")
    if isinstance(st.get("round"), int) and isinstance(b.get("attempts"), int) and st["round"] > b["attempts"]:
        head += " ⚠️ 試行が上限を超えている（周 %d / 上限 %d）" % (st["round"], b["attempts"])

    ds = diff_stats(root, st.get("base_ref"), contract)
    if ds is None:
        change = "変更: ❓（base_ref が無いか git で差分が取れない）"
    else:
        scope = ("scope 内 %d / scope 外 %d" % (ds["in_scope"], ds["out_scope"])) if ds["in_scope"] is not None else "scope 内 / 外 ❓（契約が読めない）"
        change = "変更: %d ファイル / +%d −%d（%s・oneshot.yaml と oneshot/ は数えない）" % (ds["files"], ds["add"], ds["del"], scope)

    ck = st.get("checker")
    if isinstance(ck, dict):
        checker = "Checker: 指摘 %s → 残 %s（dev-reviewer %s 周・dev-tester %s 周）" % (
            ck.get("findings", "❓"), ck.get("remaining", "❓"), ck.get("reviewer_rounds", "❓"), ck.get("tester_rounds", "❓"))
    else:
        checker = "Checker: 記録なし"

    rf = st.get("red_first") if isinstance(st.get("red_first"), dict) else None
    if rf and isinstance(rf.get("red"), int) and isinstance(rf.get("total"), int):
        rf_s = "red-first: 実装前 dod %d 本中 %d 本 red %s" % (rf["total"], rf["red"], "✅" if rf["red"] > 0 else "❌（最初から全部緑）")
    else:
        rf_s = "red-first: ❓ 記録なし"
    mut = [m for m in (st.get("mutation") or []) if isinstance(m, dict)]
    mred = sum(1 for m in mut if m.get("result") == "red")
    if mut:
        mu_s = "mutation-first: new %d 本中 %d 本が壊すと red %s" % (len(news), mred, "✅" if mred == len(news) and news else "❌")
    else:
        mu_s = "mutation-first: 未実施 ❓"
    counts = [e.get("count") for e in news]
    if news and all(isinstance(x, int) for x in counts):
        cnt_s = "new の実行件数: %d 件（0 件なら赤）" % sum(counts)
    else:
        cnt_s = "new の実行件数: ❓（取れない dod がある・0 件と書かない）"

    viol = st.get("violations") or []
    prot = st.get("protected")
    if isinstance(prot, dict) and prot:
        m = len(prot)
    elif contract is not None:
        m = len(O.forbid_paths(contract)) + len(O.gate_config(contract))
    else:
        m = None
    fb = "forbid への変更: %d 件（%s パス走査）" % (len(viol), m if m is not None else "❓")

    it = st.get("interrupted")
    if isinstance(it, dict) and it:
        ph = BUILD_PHASE.get(str(it.get("phase") or ""), "?")
        intr = "割り込み: 周 %s で人が割り込み → /build Phase %s へ移行" % (it.get("round", "?"), ph)
    else:
        intr = "割り込み: なし"

    status = str(st.get("status") or "❓")
    st_line = "状態: %s" % status + (("（PR %s）" % st["pr"]) if st.get("pr") else "")
    if str(st.get("blocker") or "").strip():
        st_line += "・blocker: %s" % str(st["blocker"]).strip()

    not_done = [x for x in (st.get("not_done") or []) if str(x).strip()]
    findings = [str(x) for x in (st.get("findings") or []) if str(x).strip()]
    for mm in mut:
        r = mm.get("result")
        tgt = "dod[%s] `%s`" % (mm.get("index", "?"), mm.get("mutate", "?"))
        if r == "green":
            findings.append("%s — 壊しても緑のまま。測定器が壊れている（実装を直しても意味がない）" % tgt)
        elif r == "no-effect":
            findings.append("%s — 当てても何も変わらなかった。1 手を書き直す（実装で行が変わった可能性）" % tgt)
        elif r == "error":
            findings.append("%s — 当てられなかった（コマンドの失敗）" % tgt)
    if rf and rf.get("red") == 0:
        findings.append("red-first で最初から全部緑だった — 「やることが無い」か「測定器が壊れている」")
    if news and any(isinstance(x, int) and x == 0 for x in counts):
        findings.append("new の実行件数が 0 件の dod がある — 絞り込みが外れている")
    if ds and ds["out_list"]:
        findings.append("scope 外の変更 %d ファイル: %s" % (len(ds["out_list"]), ", ".join(ds["out_list"][:10])))
    for v in viol:
        findings.append("forbid への変更: %s" % v)

    rf_green = (rf["total"] - rf["red"]) if rf and isinstance(rf.get("total"), int) and isinstance(rf.get("red"), int) else "❓"
    L = ["## oneshot 報告", "- " + st_line, "- " + head, "- " + change, "- " + checker,
         "- %s / %s / %s" % (rf_s, mu_s, cnt_s), "- " + fb, "- " + intr, "## やらなかったこと"]
    if not_done:
        L += ["- " + (x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)) for x in not_done]
    else:
        L.append("- ❌ 0 件 — 空は「見ていない」と同じ（state.json の not_done に書く）")
    L.append("## 測定器の所見")
    if findings:
        L += ["- " + f for f in findings]
    else:
        L.append("- なし（red-first で最初から緑だった dod: %s 本・壊しても緑のまま: 0 本）" % rf_green)
    summary = {"status": status, "dod_green": green, "dod_total": len(dod), "round": st.get("round"),
               "attempts": b.get("attempts"), "elapsed_s": el, "tokens_used": used, "diff": ds,
               "violations": len(viol), "not_done": len(not_done), "findings": len(findings)}
    return {"markdown": "\n".join(L) + "\n", "summary": summary, "ok": bool(not_done)}, ""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in argv:
        return _self_test()
    ap = argparse.ArgumentParser(prog="oneshot-report.py")
    ap.add_argument("--root")
    ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    root = os.path.abspath(a.root) if a.root else O.repo_root()
    r, err = build(root)
    if r is None:
        print(json.dumps({"ok": False, "exit": 2, "error": err}, ensure_ascii=False) if a.json else "❓ " + err + "（exit 2）")
        return 2
    rc = 0 if r["ok"] else 1
    if a.json:
        print(json.dumps(dict(r, exit=rc), ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(r["markdown"])
        if rc:
            print("\n❌ 「やらなかったこと」が 0 件 — 報告書として出さない（exit 1）")
    return rc


# ─────────────────────────────── self-test ───────────────────────────────

def _self_test() -> int:
    import contextlib
    import io
    import tempfile
    ok = ng = 0

    def check(name, cond, detail=""):
        nonlocal ok, ng
        if cond:
            ok += 1
            print("  ✅ " + name)
        else:
            ng += 1
            print("  ❌ " + name + (("  — " + str(detail)[:400]) if detail else ""))

    def run(argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def w(root, rel, text):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p) or root, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)

    def g(root, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"] + list(args),
                              cwd=root, capture_output=True, text=True)

    print("── oneshot-report self-test ──")
    with tempfile.TemporaryDirectory() as td:
        rc, out = run(["--root", td])
        check("state が無い → exit 2（0 件と書かない）", rc == 2 and "state.json" in out, out)
        w(td, "oneshot/state.json", "{壊れた")
        rc, out = run(["--root", td])
        check("state が読めない → exit 2", rc == 2, out)

    with tempfile.TemporaryDirectory() as repo:
        g(repo, "init", "-q")
        w(repo, "src/a.ts", "export const a = 1\n")
        w(repo, "other/b.ts", "export const b = 1\n")
        w(repo, "oneshot.yaml", "goal: 'x'\ndod:\n  - {kind: new, cmd: 'npm test', count: 'Tests:.*?(\\d+) passed', mutate: 'sed -i.bak s/a/b/ src/a.ts && rm -f src/a.ts.bak'}\n"
                                "  - {kind: regression, cmd: 'npm run typecheck'}\n  - {kind: boundary, cmd: 'bash scripts/harness-check.sh'}\n"
                                "scope: {paths: ['src/']}\nforbid: {paths: ['infra/'], gate_config: ['floor.json']}\n"
                                "budget: {attempts: 3, minutes: 240, tokens: 400000}\nlevel: 1\n")
        g(repo, "add", "-A")
        g(repo, "commit", "-q", "-m", "base")
        base = g(repo, "rev-parse", "HEAD").stdout.strip()
        w(repo, "src/a.ts", "export const a = 2\nexport const c = 3\n")    # scope 内・+2 −1
        w(repo, "src/new.ts", "export const n = 1\n")                        # scope 内・未追跡 +1
        w(repo, "other/b.ts", "export const b = 2\n")                        # scope 外・+1 −1
        w(repo, "oneshot/plan.md", "# plan\n")                               # 成果物（数えない）
        st = {
            "schema": 1, "status": "delivered", "goal": "x", "started_at": "2026-09-25T00:00:00Z", "pr": "#12",
            "budget": {"attempts": 3, "minutes": 240, "tokens": 400000}, "blocker": "", "phase": "deliver", "round": 2,
            "base_ref": base, "protected": {"oneshot.yaml": "h", "floor.json": "h", "infra/": "h", "oneshot/": "h", ".claude/": "h"},
            "dod": [{"kind": "new", "cmd": "npm test", "result": "green", "exit": 0, "count": 12},
                    {"kind": "regression", "cmd": "npm run typecheck", "result": "green", "exit": 0, "count": None},
                    {"kind": "boundary", "cmd": "bash scripts/harness-check.sh", "result": "green", "exit": 0, "count": None}],
            "red_first": {"at": "2026-09-25T00:01:00Z", "red": 3, "total": 3},
            "mutation": [{"index": 0, "mutate": "sed …", "result": "red", "changed": ["src/a.ts"]}],
            "tokens": {"used": None, "how": "transcript が読めない"}, "note": "", "last_diff": "",
            "history": [{"round": 1, "at": "2026-09-25T00:20:00Z", "green": 2, "total": 3, "violations": 0},
                        {"round": 2, "at": "2026-09-25T00:41:00Z", "green": 3, "total": 3, "violations": 0}],
            "violations": [], "not_done": ["scope 外で必要と分かったもの: infra/ の環境変数追加（forbid）→ 人へ"],
            "findings": [], "interrupted": None,
        }
        w(repo, "oneshot/state.json", json.dumps(st, ensure_ascii=False))
        rc, out = run(["--root", repo])
        check("報告書を出す（exit 0）", rc == 0, out)
        check("状態と PR", "状態: delivered（PR #12）" in out, out)
        check("dod の緑 / 全体・試行 / 上限", "dod: 3 本中 3 本 green（試行 2 / 上限 3" in out, out)
        check("経過は終わった周の時刻まで（41 分 / 240 分）", "経過 41 分 / 240 分" in out, out)
        check("トークンが取れない → ❓（理由つき）", "トークン ❓ / 400k（transcript が読めない）" in out, out)
        check("変更: 未追跡を含め 3 ファイル・scope 内 2 / scope 外 1", "変更: 3 ファイル / +4 −2（scope 内 2 / scope 外 1" in out, out)
        check("oneshot/ と oneshot.yaml は数えない", "plan.md" not in out.split("## やらなかったこと")[0], out)
        check("Checker の記録が無ければ「記録なし」", "Checker: 記録なし" in out)
        check("red-first・mutation-first・実行件数", "実装前 dod 3 本中 3 本 red ✅" in out and "new 1 本中 1 本が壊すと red ✅" in out
              and "new の実行件数: 12 件" in out, out)
        check("forbid への変更 0 件（走査 5 パス）", "forbid への変更: 0 件（5 パス走査）" in out, out)
        check("割り込み: なし", "割り込み: なし" in out)
        check("scope 外の変更を所見に出す", "scope 外の変更 1 ファイル: other/b.ts" in out, out)
        check("やらなかったことを出す", "infra/ の環境変数追加" in out)

        st2 = dict(st, not_done=[])
        w(repo, "oneshot/state.json", json.dumps(st2, ensure_ascii=False))
        rc, out = run(["--root", repo])
        check("やらなかったことが 0 件 → exit 1（見ていないと同じ）", rc == 1 and "見ていない" in out, out[-200:])
        # 案内どおり not_done に書けば通る
        w(repo, "oneshot/state.json", json.dumps(dict(st2, not_done=["仕様の曖昧点: 置き場所を仮置き"]), ensure_ascii=False))
        rc, out = run(["--root", repo])
        check("案内どおり not_done に 1 行書くと通る（exit 0）", rc == 0, out[-200:])

        st3 = dict(st, status="escalated", blocker="dod[0] が CAP まで赤", pr=None, tokens={"used": 310000, "how": "transcript"},
                   mutation=[{"index": 0, "mutate": "sed …", "result": "green"}, {"index": 0, "mutate": "sed2", "result": "no-effect"}],
                   red_first={"red": 0, "total": 3}, interrupted={"round": 2, "phase": "implement", "at": "x"},
                   violations=["oneshot.yaml が preflight 時と違う"], checker={"findings": 5, "remaining": 0, "reviewer_rounds": 2, "tester_rounds": 3},
                   subagents=3)
        st3["dod"] = [dict(st["dod"][0], count=0, result="red")] + st["dod"][1:]
        w(repo, "oneshot/state.json", json.dumps(st3, ensure_ascii=False))
        rc, out = run(["--root", repo])
        check("escalated: blocker を状態行に", "状態: escalated・blocker: dod[0] が CAP まで赤" in out, out)
        check("トークンが取れた → 310k / 400k・subagent 回数", "トークン 310k / 400k・subagent 3 回" in out, out)
        check("Checker の記録を出す", "Checker: 指摘 5 → 残 0（dev-reviewer 2 周・dev-tester 3 周）" in out, out)
        check("所見: 壊しても緑のまま", "壊しても緑のまま" in out)
        check("所見: 当てても何も変わらなかった", "何も変わらなかった" in out)
        check("所見: red-first で最初から全部緑", "最初から全部緑" in out)
        check("所見: 実行件数 0 件", "実行件数が 0 件" in out)
        check("所見: forbid への変更", "forbid への変更: oneshot.yaml が preflight 時と違う" in out)
        check("mutation-first ❌（1 本中 0 本）", "new 1 本中 0 本が壊すと red ❌" in out, out)
        check("割り込み: 周 2 → /build Phase 3", "周 2 で人が割り込み → /build Phase 3 へ移行" in out, out)
        rc, out = run(["--root", repo, "--json"])
        j = json.loads(out) if out.strip().startswith("{") else {}
        check("--json: summary に分母（dod_total）と exit", j.get("summary", {}).get("dod_total") == 3 and j.get("exit") == 0, out[:200])

        st4 = dict(st, base_ref=None)
        w(repo, "oneshot/state.json", json.dumps(st4, ensure_ascii=False))
        rc, out = run(["--root", repo])
        check("base_ref が無い → 変更は ❓（0 と書かない）", "変更: ❓" in out, out)

    print("\n検査 %d 件: 合格 %d / 不合格 %d" % (ok + ng, ok, ng))
    if ng:
        return 1
    print("✅ PASS — state.json と git の差分だけから、分母つきで、やらなかったことを必須にして報告書を作る")
    return 0


if __name__ == "__main__":
    sys.exit(main())
