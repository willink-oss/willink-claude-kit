#!/usr/bin/env python3
"""report-shape-check.py — 報告（markdown）の「形」を機械で検査する（読み取りのみ・何も直さない）

なぜ要るか（2026-09-16・運営側の認知ドリフト見直し → 2026-09-17 に正本へ移送）:
  読む人はセッションを多数並行していて、**この会話の経緯を覚えていない**。
  「結論から書く」だけでは、会話の経緯を前提にした報告（内輪語が説明なし・依頼の言い直しなし）を
  防げなかった。型を自然言語で書いても自己申告なので守られない（原則 P1）。ここで形を数える。

検査（exit code で判定）:
  S1 4 段の見出しが順にある: 「何を言われて何をやったか」→「結論」→「詳細」→「まとめ」
  S2 判断を仰ぐ項目（`D1.` `D2.` …）は 5 点（①何を決めるか ②選択肢と結果 ③推奨と理由
     ④判断しないと何が起きるか ⑤承認レベル）を持ち、1 報告に上限 --max-decisions（既定 3）
  S3 件数は分母つき: 「N 件 / N 本 / N ファイル / N 行」の行に分母の印（/・中・のうち・走査・分母）が無い
     → advisory。**「0 件」に分母が無いのは違反**（「測れていない」を「0 件」と書かない）
  S4 内輪語（--glossary の語）は初出の行で説明されている（（…）・=・とは・:）→ advisory
  S5 「別件:」の行数を数える（本筋から外れる話を混ぜていないかの目安・違反にはしない）

  --strict で advisory も違反にする。
  exit 0 = 形が揃っている / 1 = 違反あり / 2 = 走査できない（空・読めない）

usage:
  report-shape-check.py <report.md|->  [--glossary terms.json] [--max-decisions 3] [--strict] [--json]
  report-shape-check.py --self-test
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys

SECTIONS = ("何を言われて", "結論", "詳細", "まとめ")
FIVE = (("①", "何を決める"), ("②", "選択肢"), ("③", "推奨"), ("④", "判断しないと"), ("⑤", "承認レベル"))
COUNT_RE = re.compile(r"(\d[\d,]*)\s*(件|本|ファイル|行)")
DENOM_MARKS = ("/", "中", "のうち", "うち", "走査", "分母", "上限", "分の")
EXPLAIN_MARKS = ("（", "(", "=", "とは", ":", "：")
DECISION_RE = re.compile(r"^(?:#+\s*|\*\*)?D(\d+)[.．]", re.M)
HEADING_RE = re.compile(r"^#+\s+(.*)$", re.M)


def analyze(text: str, glossary: list[str] | None = None, max_decisions: int = 3) -> dict:
    lines = text.splitlines()
    fails: list[str] = []
    advisories: list[str] = []

    # S1 見出しの順序
    headings = [m.group(1) for m in HEADING_RE.finditer(text)]
    pos = 0
    found = []
    for key in SECTIONS:
        hit = next((i for i in range(pos, len(headings)) if key in headings[i]), None)
        if hit is None:
            fails.append(f"S1 見出しに「{key}」を含む行が（この順で）無い — `## {key}` を足す")
        else:
            found.append(key)
            pos = hit + 1

    # S2 判断項目の 5 点と上限
    marks = list(DECISION_RE.finditer(text))
    n_dec = len(marks)
    five_ok = 0
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        block = text[m.start():end]
        missing = [f"{sym}{kw}" for sym, kw in FIVE if sym not in block and kw not in block]
        if missing:
            fails.append(f"S2 D{m.group(1)} に 5 点のうち {missing} が無い")
        else:
            five_ok += 1
    if n_dec > max_decisions:
        fails.append(f"S2 判断項目 {n_dec} 件 > 上限 {max_decisions} — 優先順位をつけ、残りは件数だけにする")

    # S3 分母
    n_count_lines = 0
    n_nodenom = 0
    for ln in lines:
        ms = COUNT_RE.findall(ln)
        if not ms or ln.lstrip().startswith("#"):
            continue
        n_count_lines += 1
        if any(mk in ln for mk in DENOM_MARKS):
            continue
        n_nodenom += 1
        zeros = [n for n, _u in ms if n.replace(",", "") == "0"]
        if zeros:
            fails.append(f"S3 「0 {ms[0][1]}」に分母が無い（測れていないのか 0 なのかが分からない）: {ln.strip()[:60]}")
        else:
            advisories.append(f"S3 分母なしの件数: {ln.strip()[:60]}")

    # S4 内輪語
    n_terms = 0
    n_unexplained = 0
    for term in glossary or []:
        n_terms += 1
        first = next((ln for ln in lines if term in ln), None)
        if first is None:
            continue
        if not any(mk in first for mk in EXPLAIN_MARKS):
            n_unexplained += 1
            advisories.append(f"S4 内輪語「{term}」が初出の行で説明されていない — 「{term}（普通の言葉で）」の形にする")

    # S5 別件
    n_aside = sum(1 for ln in lines if ln.lstrip().startswith("別件:") or ln.lstrip().startswith("- 別件:"))

    return {
        "sections": f"{len(found)}/{len(SECTIONS)}",
        "decisions": n_dec, "max_decisions": max_decisions, "five_point_ok": f"{five_ok}/{n_dec}",
        "count_lines": n_count_lines, "count_lines_without_denominator": n_nodenom,
        "glossary_terms": n_terms, "glossary_unexplained": n_unexplained,
        "asides": n_aside,
        "fails": fails, "advisories": advisories,
    }


def render(r: dict, strict: bool) -> tuple[str, int]:
    out = [
        "# report-shape-check",
        f"- 見出し {r['sections']}・判断 {r['decisions']} 件（上限 {r['max_decisions']}・5 点 {r['five_point_ok']}）"
        f"・分母なしの件数 {r['count_lines_without_denominator']}/{r['count_lines']} 行"
        f"・内輪語 未説明 {r['glossary_unexplained']}/{r['glossary_terms']} 語・別件 {r['asides']} 行",
    ]
    for f in r["fails"]:
        out.append(f"  ❌ {f}")
    for a in r["advisories"]:
        out.append(f"  ⚠️ {a}")
    bad = len(r["fails"]) + (len(r["advisories"]) if strict else 0)
    out.append(("❌ 違反 %d 件" % bad) if bad else "✅ 形が揃っている")
    return "\n".join(out), (1 if bad else 0)


def self_test() -> int:
    good = """## 1. 何を言われて、何をやったか
A を頼まれ B をした。
## 2. 結論
できた。
## 3. 詳細
走査 10 件のうち 3 件。sweep（定期の一括処理）を回した。
別件: 無関係な発見。
## 4. まとめ
**D1.** 続けるか
- ①何を決めるか ②選択肢と結果 ③推奨と理由 ④判断しないと何が起きるか ⑤承認レベル L1
"""
    checks: list[tuple[str, bool]] = []

    def ck(name, cond):
        checks.append((name, bool(cond)))

    r = analyze(good, ["sweep"])
    ck("良い報告は違反 0", not r["fails"] and not r["advisories"] and r["sections"] == "4/4")
    ck("別件の行を数える", r["asides"] == 1)
    r = analyze(good.replace("## 2. 結論\n", "## 2. 判定\n"))
    ck("「結論」の見出しが無いと違反", any("S1" in f and "結論" in f for f in r["fails"]))
    ck("代替手順で通る: 見出しを足すと通る", not analyze(good)["fails"])
    r = analyze(good.replace("## 3. 詳細", "## 3. まとめ先取り").replace("## 4. まとめ", "## 4. 詳細"))
    ck("順序が逆でも違反（詳細 → まとめ）", any("S1" in f for f in r["fails"]))
    r = analyze(good.replace("⑤承認レベル L1", ""))
    ck("5 点のうち ⑤ が無いと違反", any("S2 D1" in f for f in r["fails"]))
    many = good + "".join(f"**D{i}.** x\n- ①何を決めるか ②選択肢 ③推奨 ④判断しないと ⑤承認レベル\n" for i in (2, 3, 4))
    r = analyze(many)
    ck("判断 4 件は上限 3 を超えて違反", any("上限" in f for f in r["fails"]) and r["decisions"] == 4)
    ck("--max-decisions 4 なら通る", not analyze(many, max_decisions=4)["fails"])
    r = analyze(good.replace("走査 10 件のうち 3 件", "0 件だった"))
    ck("「0 件」に分母が無いと違反", any("S3" in f and "0 件" in f for f in r["fails"]))
    r = analyze(good.replace("走査 10 件のうち 3 件", "3 件直した"))
    ck("「3 件」に分母が無いのは advisory（違反にしない）", not r["fails"] and any("S3" in a for a in r["advisories"]))
    ck("--strict なら advisory も落ちる", render(r, strict=True)[1] == 1 and render(r, strict=False)[1] == 0)
    r = analyze(good.replace("sweep（定期の一括処理）", "sweep"), ["sweep"])
    ck("内輪語が未説明なら advisory", r["glossary_unexplained"] == 1 and any("S4" in a for a in r["advisories"]))
    ck("代替手順で通る: 「語（説明）」にすると 0", analyze(good, ["sweep"])["glossary_unexplained"] == 0)
    ck("語彙に無い語は数えない", analyze(good, ["gate"])["glossary_terms"] == 1 and analyze(good, ["gate"])["glossary_unexplained"] == 0)
    def quiet(text):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main_from_text(text)
    ck("空入力は exit 2", quiet("") == 2)
    ck("良い報告は exit 0 / 悪い報告は exit 1", quiet(good) == 0 and quiet(good.replace("## 2. 結論\n", "")) == 1)

    ng = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(("  ✅ " if ok else "  ❌ ") + n)
    print(f"self-test: {len(checks) - len(ng)}/{len(checks)} passed")
    return 0 if not ng else 1


def main_from_text(text: str, glossary=None, max_decisions=3, strict=False, as_json=False) -> int:
    if not text.strip():
        print("❗ 入力が空 — 走査できない（0 件とは言わない）", file=sys.stderr)
        return 2
    r = analyze(text, glossary, max_decisions)
    body, rc = render(r, strict)
    if as_json:
        print(json.dumps(dict(r, exit=rc), ensure_ascii=False))
    else:
        print(body)
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", nargs="?", help="markdown ファイル（- で stdin）")
    ap.add_argument("--glossary", help='内輪語の JSON（["sweep", ...] または {"terms": [...]}）')
    ap.add_argument("--max-decisions", type=int, default=3)
    ap.add_argument("--strict", action="store_true", help="advisory も違反にする")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.report:
        ap.print_usage()
        return 2
    try:
        text = sys.stdin.read() if a.report == "-" else open(a.report, encoding="utf-8").read()
    except OSError as e:
        print(f"❗ 読めない: {e}", file=sys.stderr)
        return 2
    glossary = None
    if a.glossary:
        try:
            g = json.load(open(a.glossary, encoding="utf-8"))
            glossary = list(g.get("terms", [])) if isinstance(g, dict) else list(g)
        except (OSError, ValueError) as e:
            print(f"❗ glossary が読めない: {e}", file=sys.stderr)
            return 2
    return main_from_text(text, glossary, a.max_decisions, a.strict, a.json)


if __name__ == "__main__":
    sys.exit(main())
