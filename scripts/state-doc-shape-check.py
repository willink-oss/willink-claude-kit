#!/usr/bin/env python3
"""state-doc-shape-check.py — 「上から順に読む」状態文書の形を機械で守る（読み取りのみ・何も直さない）

なぜ要るか（2026-09-16・運営側の action-list が 1,970 行 / 完了 33 節まで太った → 2026-09-17 に型を正本へ）:
  引き継ぎ文書（HANDOFF・action list）は読む人が「上から順に」読む前提だが、完了した節が
  残り続けると、読める量を超える。**文脈で届かないものは存在しない**のは、読む側が人でも同じ。
  原因は「上限が無い」ことなので、上限を数値で持ち、超えたら赤にする。

検査（exit code で判定）:
  1. 行数 <= floor。floor は文書自身の印 `<!-- shape: floor=N -->` から読む（無ければ --floor）。
     どちらも無ければ exit 2（測れない・0 と言わない）
  2. floor は**下げる方向のみ**: git で HEAD の同じ文書に印があり、いまの値がそれより大きければ違反
     （掃除せずに上限を上げる逃げ道を塞ぐ）
  3. 完了の見出し（`## ✅` `### 1. ✅ 完了 …` `🗂️` `⏹` で始まる H2/H3）が残っていない — archive へ移す
  4. 表の中に取り消し線の行（`| ~~…~~`）が残っていない — 決まったものは消す（記録は台帳 / ADR にある）

  exit 0 = 形が揃っている / 1 = 違反あり / 2 = 測れない（floor 無し・読めない）

usage:
  state-doc-shape-check.py <doc.md> [--floor N] [--done-prefixes "✅,🗂️,⏹"] [--json]
  state-doc-shape-check.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

MARK_RE = re.compile(r"<!--\s*shape:\s*floor=(\d+)\s*-->")
DONE_DEFAULT = ("✅", "🗂️", "⏹")
HEAD_RE = re.compile(r"^(#{2,3})\s+(?:\d+(?:\.\d+)*\.?\s+)?(.*)$", re.M)
STRUCK_ROW_RE = re.compile(r"^\|\s*~~", re.M)


def floor_of(text: str) -> int | None:
    m = MARK_RE.search(text)
    return int(m.group(1)) if m else None


def head_text(path: str) -> str | None:
    """git HEAD の同じファイル。git 外・未追跡なら None（比較しない = ratchet は不明）。"""
    d = os.path.dirname(os.path.abspath(path)) or "."
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=d, capture_output=True, text=True, check=True).stdout.strip()
        # realpath で揃える: macOS の tempdir は /var → /private/var の symlink で、git の toplevel は解決済み、
        # path は未解決のままなので relpath が ../../ に化け、HEAD の印が読めず ratchet を見逃していた（2026-09-17）
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(top))
        r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=top, capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None
    except (subprocess.CalledProcessError, OSError):
        return None


def analyze(text: str, floor: int | None, prev_floor: int | None, done_prefixes=DONE_DEFAULT) -> dict:
    fails: list[str] = []
    n_lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    if floor is None:
        return {"lines": n_lines, "floor": None, "fails": ["floor が無い — 文書の先頭に `<!-- shape: floor=N -->` を書く（測れないので exit 2）"],
                "measurable": False}
    if n_lines > floor:
        fails.append(f"行数 {n_lines} > floor {floor} — 完了した節を archive へ移す（floor を上げない）")
    if prev_floor is not None and floor > prev_floor:
        fails.append(f"floor を {prev_floor} → {floor} に上げている — 下げる方向のみ（掃除して行数を減らす）")
    heads = HEAD_RE.findall(text)
    done = [t for _lvl, t in heads if t.startswith(tuple(done_prefixes))]
    if done:
        fails.append(f"完了の見出しが残っている {len(done)}/{len(heads)} 節 — archive ファイルへ移し、本体は 1 行にする: "
                     + " / ".join(d[:30] for d in done[:5]))
    struck = STRUCK_ROW_RE.findall(text)
    n_rows = sum(1 for ln in text.splitlines() if ln.startswith("|"))
    if struck:
        fails.append(f"取り消し線の行が表に残っている {len(struck)}/{n_rows} 行 — 決まった行は消す（記録は台帳 / ADR）")
    return {"lines": n_lines, "floor": floor, "prev_floor": prev_floor,
            "headings": len(heads), "done_headings": len(done),
            "table_rows": n_rows, "struck_rows": len(struck), "fails": fails, "measurable": True}


def render(r: dict) -> str:
    if not r["measurable"]:
        return "# state-doc-shape-check\n  ❗ " + r["fails"][0]
    out = ["# state-doc-shape-check",
           f"- 行数 {r['lines']} / floor {r['floor']}（HEAD の floor: {r['prev_floor'] if r['prev_floor'] is not None else '不明'}）"
           f"・完了の見出し {r['done_headings']}/{r['headings']}・取り消し線の行 {r['struck_rows']}/{r['table_rows']}"]
    out += [f"  ❌ {f}" for f in r["fails"]]
    out.append(f"❌ 違反 {len(r['fails'])} 件" if r["fails"] else "✅ 形が揃っている")
    return "\n".join(out)


def check_file(path: str, floor_arg: int | None, done_prefixes, as_json=False) -> int:
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        print(f"❗ 読めない: {e}", file=sys.stderr)
        return 2
    floor = floor_of(text) if floor_of(text) is not None else floor_arg
    prev = head_text(path)
    prev_floor = floor_of(prev) if prev else None
    r = analyze(text, floor, prev_floor, done_prefixes)
    print(json.dumps(r, ensure_ascii=False) if as_json else render(r))
    if not r["measurable"]:
        return 2
    return 1 if r["fails"] else 0


def self_test() -> int:
    checks = []

    def ck(name, cond):
        checks.append((name, bool(cond)))

    good = "<!-- shape: floor=20 -->\n# doc\n## 🎯 いま\n- a\n## 次\n| # | 何 |\n|---|---|\n| 1 | x |\n"
    ck("良い文書は違反 0", not analyze(good, 20, 20)["fails"])
    ck("行数が floor を超えると違反", any("行数" in f for f in analyze(good + "x\n" * 20, 20, 20)["fails"]))
    ck("floor を上げると違反（HEAD 20 → 30）", any("上げている" in f for f in analyze(good, 30, 20)["fails"]))
    ck("代替手順で通る: floor を下げるのは通る（20 → 15）", not analyze(good, 15, 20)["fails"])
    ck("HEAD に印が無ければ ratchet は不明で違反にしない", not analyze(good, 30, None)["fails"])
    ck("完了の見出し（### 1. ✅ 完了）が残っていると違反", any("完了の見出し" in f for f in analyze(good + "### 1. ✅ 完了 — x\n", 20, 20)["fails"]))
    ck("完了の見出しを archive へ移すと通る（代替手順）", not analyze(good, 20, 20)["fails"])
    ck("取り消し線の行が表に残っていると違反", any("取り消し線" in f for f in analyze(good + "| ~~2~~ | 決まった |\n", 20, 20)["fails"]))
    ck("floor が無ければ measurable=False", analyze(good, None, None)["measurable"] is False)
    ck("--done-prefixes で印を差し替えられる", any("完了の見出し" in f for f in analyze(good + "## DONE x\n", 20, 20, ("DONE",))["fails"]))

    # 実ファイル + git（hermetic）: HEAD の印との比較・exit code
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q", d], check=True)
        p = os.path.join(d, "s.md")
        open(p, "w", encoding="utf-8").write(good)
        subprocess.run(["git", "-C", d, "add", "s.md"], check=True)
        subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"], check=True)
        import contextlib, io
        def run(fl=None, path=p):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return check_file(path, fl, DONE_DEFAULT)
        ck("git 追跡の良い文書は exit 0", run() == 0)
        open(p, "w", encoding="utf-8").write(good.replace("floor=20", "floor=40"))
        ck("HEAD より floor を上げた作業木は exit 1", run() == 1)
        open(p, "w", encoding="utf-8").write(good.replace("<!-- shape: floor=20 -->\n", ""))
        ck("印が無く --floor も無ければ exit 2", run() == 2)
        ck("印が無くても --floor があれば測れる（exit 0）", run(20) == 0)
        ck("無いファイルは exit 2", run(10, os.path.join(d, "none.md")) == 2)

    ng = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(("  ✅ " if ok else "  ❌ ") + n)
    print(f"self-test: {len(checks) - len(ng)}/{len(checks)} passed")
    return 0 if not ng else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("doc", nargs="?")
    ap.add_argument("--floor", type=int, help="文書に印が無いときの上限（印があれば印が勝つ）")
    ap.add_argument("--done-prefixes", default=",".join(DONE_DEFAULT), help="完了とみなす見出しの先頭（カンマ区切り）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.doc:
        ap.print_usage()
        return 2
    return check_file(a.doc, a.floor, tuple(x for x in a.done_prefixes.split(",") if x), a.json)


if __name__ == "__main__":
    sys.exit(main())
