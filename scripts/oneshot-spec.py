#!/usr/bin/env python3
"""oneshot-spec.py — /oneshot の Phase −1（仕様を詰める対話）の機械側。候補を出すだけで、決めるのは利用者。

なぜ在るか（2026-09-25・docs/design/oneshot-mode.md §2.1・§8-2）:
  無人区間の品質は契約（oneshot.yaml）に書いた DoD の品質と同じにしかならない。だから契約を作る段は
  人と対話する。ただし「どのコマンドで完了を判定できるか」「実行件数をどう取るか」「何を壊せば赤に
  なるべきか」を毎回人がゼロから考えると、詰めが甘くなるか、時間がかかりすぎる。
  機械は **候補** を出し、利用者が選んで確定する。**数値の既定値は置かない**（§7-2）。

サブコマンド:
  detect [--root R] [--json]
      stack を検出し、DoD の候補（新規 / 回帰 / 境界）と runner ごとの実行件数の取り方（count）を出す。
      何も検出できなければ exit 2（「無い」ではなく「測れていない」）。
  mutate-candidates --files F [F …] [--root R] [--max N] [--json]
      触る予定のファイルから「壊す 1 手」を決定論の sed で提示する。GNU と BSD の両方で動く書き方
      （`sed -i.bak … && rm -f ….bak`）。候補ごとに一時コピーへ実際に当て、ファイルが変わらない候補は出さない。
      exit 0 = 候補あり / 1 = 読めたが候補 0（人が書く）/ 2 = 読めるファイルが無い
  budget --deadline-hours H --fail-tolerance N --watch-every-min M --subagents S [--json]
      問 6 の答えから budget の値を導いて **提示** する（確定は利用者）。どれか欠ければ exit 2。
  init [--root R]
      oneshot.yaml の下書きを書く（DoD は候補から・scope と budget は空・forbid の既定・level 1）。
      既にあれば上書きしない（exit 1）。
  spec [--root R]
      oneshot.yaml から oneshot/spec.md（人が読んで「走らせてよい」と言う文書）を作る。
      契約に問題があれば書かない（phase-1 の問題 = exit 1・reject = exit 2）。
  --self-test
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _oneshot as O  # noqa: E402

# ─────────────────────────────── runner ごとの実行件数 ───────────────────────────────
# 実出力の例は self-test で当てる。色つき出力（TTY）だと一致しないことがあるので、CI 相当の非 TTY で回す前提。
COUNT = {
    "jest": r"Tests:.*?(\d+) passed",
    "vitest": r"Tests\s+.*?(\d+) passed",
    "mocha": r"(\d+) passing",
    "pytest": r"(\d+) passed",
    "go": r"(?m)^\s*--- PASS",
    "xctest": r"Test Suite 'All tests' (?:passed|failed)[^\n]*\n\s*Executed (\d+) tests?",
    "flutter": r"\+(\d+): All tests passed",
    "cargo": r"test result: \w+\. (\d+) passed",
}

COUNT_SAMPLES = {  # runner → (出力の例, 期待する件数)
    "jest": ("PASS src/a.test.ts\nTests:       12 passed, 12 total\nSnapshots:   0 total\n", 12),
    "vitest": (" ✓ src/a.test.ts (3)\n Test Files  1 passed (1)\n      Tests  12 passed (12)\n", 12),
    "mocha": ("  suite\n    ✓ works\n\n  12 passing (34ms)\n", 12),
    "pytest": ("..........\n============ 12 passed in 0.34s ============\n", 12),
    "go": ("=== RUN   TestA\n--- PASS: TestA (0.00s)\n=== RUN   TestB\n--- PASS: TestB (0.00s)\nok  \tpkg\t0.01s\n", 2),
    "xctest": ("Test Suite 'AppTests' passed at 2026-09-25 10:00:00.000.\n\t Executed 4 tests, with 0 failures (0 unexpected) in 0.1 (0.1) seconds\n"
               "Test Suite 'All tests' passed at 2026-09-25 10:00:00.100.\n\t Executed 16 tests, with 0 failures (0 unexpected) in 0.5 (0.6) seconds\n", 16),
    "flutter": ("00:03 +11: loading\n00:05 +12: All tests passed!\n", 12),
    "cargo": ("test result: ok. 5 passed; 0 failed; 0 ignored\n\ntest result: ok. 2 passed; 0 failed; 0 ignored\n", 7),
}

UI_EXT = {".tsx": "react", ".jsx": "react", ".vue": "vue", ".svelte": "svelte", ".dart": "flutter", ".php": "php", ".html": "html"}
SKIP_DIRS = {".git", "node_modules", "dist", "build", ".next", "DerivedData", ".build", "Pods", "vendor", "__pycache__", ".venv", "venv", "target", ".dart_tool"}


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _walk(root, max_depth=4):
    base = root.rstrip(os.sep).count(os.sep)
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".") or x == ".github"]
        if d.count(os.sep) - base >= max_depth:
            dirs[:] = []
        yield d, files


def _cand(kind, cmd, why, source, count=None, runner=None, fill=False):
    c = {"kind": kind, "cmd": cmd, "why": why, "source": source}
    if count is not None:
        c["count"] = count
    if runner:
        c["runner"] = runner
    if fill:
        c["要記入"] = True
    return c


def _pkg_manager(d):
    if os.path.exists(os.path.join(d, "pnpm-lock.yaml")):
        return "pnpm"
    if os.path.exists(os.path.join(d, "yarn.lock")):
        return "yarn"
    return "npm"


def _detect_node(root, rel, pkg, out, stacks):
    d = os.path.join(root, rel)
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    deps = {}
    for k in ("dependencies", "devDependencies"):
        if isinstance(pkg.get(k), dict):
            deps.update(pkg[k])
    pm = _pkg_manager(d) if os.path.exists(os.path.join(d, "package.json")) else "npm"
    pre = ("cd %s && " % rel) if rel not in ("", ".") else ""
    src = os.path.join(rel, "package.json") if rel not in ("", ".") else "package.json"
    test = str(scripts.get("test") or "")
    runner = None
    for r in ("vitest", "jest", "mocha"):
        if r in test or r in deps:
            runner = r
            break
    stacks.append({"stack": "node", "where": rel or ".", "runner": runner or "?"})
    if test and "no test specified" not in test:
        out["new"].append(_cand("new", pre + ("%s test" % pm), "package.json の test（%s）" % (runner or "runner 不明"), src,
                                count=COUNT.get(runner) if runner else None, runner=runner, fill=runner is None))
        out["regression"].append(_cand("regression", pre + ("%s test" % pm), "既存のテストスイート全体（壊していないこと）", src))
    for name in ("lint", "typecheck", "type-check", "tsc", "build"):
        if name in scripts:
            out["regression"].append(_cand("regression", pre + "%s run %s" % (pm, name), "package.json の %s" % name, src))


def _make_targets(text):
    ts = []
    for m in re.finditer(r"(?m)^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)", text):
        if m.group(1) not in ts:
            ts.append(m.group(1))
    return ts


def detect(root):
    out = {"new": [], "regression": [], "boundary": []}
    stacks, notes, markers = [], [], 0
    # node（ルートと 2 階層下まで・monorepo の functions/ web/ apps/* など）
    pkgs = []
    for d, files in _walk(root, max_depth=3):
        if "package.json" in files:
            rel = os.path.relpath(d, root)
            rel = "" if rel == "." else rel.replace(os.sep, "/")
            try:
                with open(os.path.join(d, "package.json"), encoding="utf-8") as fh:
                    pkgs.append((rel, json.load(fh)))
            except (OSError, ValueError):
                notes.append("❓ %s/package.json が読めない" % (rel or "."))
    markers += 1
    for rel, pkg in pkgs:
        if isinstance(pkg, dict):
            _detect_node(root, rel, pkg, out, stacks)
    # Makefile
    markers += 1
    mk = _read(os.path.join(root, "Makefile"))
    if mk is not None:
        ts = _make_targets(mk)
        stacks.append({"stack": "make", "where": ".", "targets": ts[:20]})
        for t in ts:
            if t == "test" or t.endswith("-test") or t.startswith("test-"):
                out["new"].append(_cand("new", "make %s" % t, "Makefile の %s（runner は中身で決まる・count は実出力で確かめる）" % t, "Makefile", fill=True))
            if t in ("check", "lint", "typecheck", "build") or t.endswith("-test"):
                out["regression"].append(_cand("regression", "make %s" % t, "Makefile の %s" % t, "Makefile"))
    # python
    markers += 1
    py = _read(os.path.join(root, "pyproject.toml")) or ""
    has_pytest = ("pytest" in py) or os.path.exists(os.path.join(root, "pytest.ini")) or \
        bool(glob.glob(os.path.join(root, "tests", "test_*.py"))) or bool(glob.glob(os.path.join(root, "test_*.py")))
    if has_pytest:
        stacks.append({"stack": "python", "where": ".", "runner": "pytest"})
        out["new"].append(_cand("new", "python3 -m pytest -q", "pytest", "pyproject.toml / tests/", count=COUNT["pytest"], runner="pytest"))
        out["regression"].append(_cand("regression", "python3 -m pytest -q", "既存のテストスイート全体", "pyproject.toml / tests/"))
        if "ruff" in py:
            out["regression"].append(_cand("regression", "ruff check .", "ruff", "pyproject.toml"))
        if "mypy" in py:
            out["regression"].append(_cand("regression", "mypy .", "mypy", "pyproject.toml"))
    # go
    markers += 1
    if os.path.exists(os.path.join(root, "go.mod")):
        stacks.append({"stack": "go", "where": ".", "runner": "go test"})
        out["new"].append(_cand("new", "go test -v ./...", "go test（-v で PASS 行を数える）", "go.mod", count=COUNT["go"], runner="go"))
        out["regression"].append(_cand("regression", "go vet ./... && go build ./...", "go vet / build", "go.mod"))
    # flutter / dart
    markers += 1
    pub = _read(os.path.join(root, "pubspec.yaml"))
    if pub is not None:
        fl = "flutter" in pub
        stacks.append({"stack": "flutter" if fl else "dart", "where": "."})
        cmd = "flutter test" if fl else "dart test"
        out["new"].append(_cand("new", cmd, cmd, "pubspec.yaml", count=COUNT["flutter"], runner="flutter"))
        out["regression"].append(_cand("regression", "flutter analyze" if fl else "dart analyze", "analyze", "pubspec.yaml"))
    # swift / xcode
    markers += 1
    if os.path.exists(os.path.join(root, "Package.swift")):
        stacks.append({"stack": "swiftpm", "where": "."})
        out["new"].append(_cand("new", "swift test", "swift test（XCTest）", "Package.swift", count=COUNT["xctest"], runner="xctest"))
    xproj = glob.glob(os.path.join(root, "*.xcodeproj")) + glob.glob(os.path.join(root, "*", "*.xcodeproj"))
    xgen = [p for p in (glob.glob(os.path.join(root, "project.yml")) + glob.glob(os.path.join(root, "*", "project.yml")))]
    if xproj or xgen:
        stacks.append({"stack": "xcode", "where": ", ".join(sorted(os.path.relpath(p, root) for p in xproj + xgen))})
        out["new"].append(_cand("new", "xcodebuild test -scheme <Scheme> -destination '<destination>'",
                                "xcodebuild test（scheme と destination は人が書く）", "xcodeproj / project.yml",
                                count=COUNT["xctest"], runner="xctest", fill=True))
    # rust
    markers += 1
    if os.path.exists(os.path.join(root, "Cargo.toml")):
        stacks.append({"stack": "rust", "where": ".", "runner": "cargo test"})
        out["new"].append(_cand("new", "cargo test", "cargo test", "Cargo.toml", count=COUNT["cargo"], runner="cargo"))
        out["regression"].append(_cand("regression", "cargo clippy -- -D warnings && cargo build", "clippy / build", "Cargo.toml"))
    # CI の job（回帰の候補・required check らしきもの）
    markers += 1
    ci_jobs = []
    for wf in sorted(glob.glob(os.path.join(root, ".github", "workflows", "*.y*ml"))):
        try:
            y = O.load_yaml(_read(wf) or "")
        except O.YamlError:
            notes.append("❓ %s が読めない（CI の job を候補にできていない）" % os.path.relpath(wf, root))
            continue
        jobs = (y or {}).get("jobs") if isinstance(y, dict) else None
        if not isinstance(jobs, dict):
            continue
        for jid, j in jobs.items():
            if not isinstance(j, dict):
                continue
            runs = [str(s.get("run")).strip().splitlines()[0] for s in (j.get("steps") or [])
                    if isinstance(s, dict) and str(s.get("run") or "").strip()]
            ci_jobs.append({"workflow": os.path.relpath(wf, root), "job": jid, "name": j.get("name") or jid, "runs": runs[:5]})
    for cj in ci_jobs:
        if cj["runs"]:
            out["regression"].append(_cand("regression", " && ".join(cj["runs"]),
                                           "CI の job「%s」の run（required check なら回帰に入れる）" % cj["name"], cj["workflow"]))
    # 境界: 触る層に対応するゲート
    markers += 1
    ui = {}
    for d, files in _walk(root, max_depth=5):
        for f in files:
            ext = os.path.splitext(f)[1]
            if ext in UI_EXT:
                top = os.path.relpath(d, root).replace(os.sep, "/").split("/")[0]
                ui.setdefault(UI_EXT[ext], set()).add(top if top != "." else ".")
            elif ext == ".swift":
                t = _read(os.path.join(d, f)) or ""
                if "import SwiftUI" in t or "import UIKit" in t:
                    top = os.path.relpath(d, root).replace(os.sep, "/").split("/")[0]
                    ui.setdefault("swiftui", set()).add(top)
    for kind, dirs in sorted(ui.items()):
        dl = " ".join(sorted(dirs))
        out["boundary"].append(_cand("boundary", "python3 <kit>/scripts/a11y-static-check.py %s" % dl,
                                     "UI（%s）を触るなら a11y ゲート（kit の a11y-static-gate・<kit> は plugin の場所）" % kind,
                                     "UI ファイル", fill=True))
        if kind == "flutter":
            out["boundary"].append(_cand("boundary", "python3 <harness>/fixtures/not-wired-values/check.py lib",
                                         "Dart の画面を触るなら not-wired-values（値が配線されていない表示を止める）", "*.dart", fill=True))
    if os.path.exists(os.path.join(root, "firestore.rules")):
        out["boundary"].append(_cand("boundary", "python3 <harness>/fixtures/firestore-rules-access/check.py firestore.rules",
                                     "rules を触るなら firestore-rules-access", "firestore.rules", fill=True))
    if os.path.exists(os.path.join(root, "scripts", "harness-check.sh")):
        out["boundary"].append(_cand("boundary", "bash scripts/harness-check.sh",
                                     "ハーネスの配線と fixture（harness-targets.json の対象）", "scripts/harness-check.sh"))
    total = sum(len(v) for v in out.values())
    return {"root": root, "markers": markers, "stacks": stacks, "candidates": out, "ci_jobs": ci_jobs,
            "notes": notes, "total": total}


def render_detect(r):
    L = ["# oneshot-spec detect — 走査したマーカー %d 種 / stack %d 件 / 候補 %d 件" % (r["markers"], len(r["stacks"]), r["total"]), ""]
    for s in r["stacks"]:
        L.append("- stack: %s（%s）%s" % (s["stack"], s.get("where", "."), ("・runner " + s["runner"]) if s.get("runner") else ""))
    for k, title in (("new", "新規の判定の候補（red-first・mutation-first の対象・count 必須）"),
                     ("regression", "回帰の候補（今動いているものを壊していないこと）"),
                     ("boundary", "境界の候補（触る層に対応するゲート）")):
        L += ["", "## %s — %d 件" % (title, len(r["candidates"][k]))]
        if r["candidates"][k]:
            L.append("| cmd | count | 出所 | なぜ |")
            L.append("|---|---|---|---|")
            for c in r["candidates"][k]:
                cnt = c.get("count") or ("❓ 実出力で確かめて書く" if k == "new" else "—")
                cmd = c["cmd"] + ("（要記入）" if c.get("要記入") else "")
                L.append("| `%s` | `%s` | %s | %s |" % (cmd, cnt, c["source"], c["why"]))
    for n in r["notes"]:
        L.append(n)
    L += ["", "候補は候補。**どれを dod にするかは利用者が決める**（count は実際に 1 回回して一致を確かめる）。"]
    return "\n".join(L)


# ─────────────────────────────── mutate 候補 ───────────────────────────────

# (名前, 正規表現, 置換) — 1 行の最初の一致だけを変える。上ほど優先。
MUTATORS = [
    ("return true → false", r"\breturn true\b", "return false"),
    ("return false → true", r"\breturn false\b", "return true"),
    ("return True → False", r"\breturn True\b", "return False"),
    ("return False → True", r"\breturn False\b", "return True"),
    ("=== → !==", r"===", "!=="),
    ("!== → ===", r"!==", "==="),
    ("== → !=", r"(?<![=!<>])==(?!=)", "!="),
    ("!= → ==", r"!=(?!=)", "=="),
    (">= → <", r" >= ", " < "),
    ("<= → >", r" <= ", " > "),
    ("> → <=", r" > ", " <= "),
    ("< → >=", r" < ", " >= "),
    ("&& → ||", r"&&", "||"),
    ("|| → &&", r"\|\|", "&&"),
    (" and → or", r" and ", " or "),
    (" or → and", r" or ", " and "),
    ("is None → is not None", r"\bis None\b", "is not None"),
    ("is not None → is None", r"\bis not None\b", "is None"),
    ("true → false", r"\btrue\b", "false"),
    ("false → true", r"\bfalse\b", "true"),
    ("True → False", r"\bTrue\b", "False"),
    ("False → True", r"\bFalse\b", "True"),
]
_COMMENT = re.compile(r"^\s*(//|#|\*|/\*|<!--|--)")
_SKIP_LINE = re.compile(r"^\s*(import |from \S+ import |#include|using |package |@)")


def _bre_escape(s, delim):
    out = []
    for ch in s:
        if ch in "\\.*[]^$" or ch == delim:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _repl_escape(s, delim):
    out = []
    for ch in s:
        if ch in "\\&" or ch == delim:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _shq(s):
    return "'" + s.replace("'", "'\\''") + "'"


def sed_command(rel, before, after):
    """GNU / BSD 両方で動く `sed -i.bak '…' file && rm -f file.bak`。区切り文字は行に無いものを選ぶ。"""
    for delim in "|#,@!%~:;":
        if delim not in before and delim not in after:
            break
    else:
        return None
    expr = "s%s^%s$%s%s%s" % (delim, _bre_escape(before, delim), delim, _repl_escape(after, delim), delim)
    return "sed -i.bak %s %s && rm -f %s" % (_shq(expr), _shq(rel), _shq(rel + ".bak"))


def _try_apply(root, rel, cmd):
    """一時ディレクトリにファイルを写して cmd を当て、変わったかを返す（本物のファイルには触らない）。"""
    with tempfile.TemporaryDirectory() as td:
        dst = os.path.join(td, rel)
        os.makedirs(os.path.dirname(dst) or td, exist_ok=True)
        shutil.copy2(os.path.join(root, rel), dst)
        with open(dst, "rb") as fh:
            before = fh.read()
        try:
            r = subprocess.run(cmd, shell=True, cwd=td, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        if r.returncode != 0 or os.path.exists(dst + ".bak"):
            return False
        with open(dst, "rb") as fh:
            return fh.read() != before


def mutate_candidates(root, files, max_per_file=3):
    res, unreadable, scanned = [], [], 0
    for f in files:
        rel = O.relpath(root, f)
        if rel is None:
            unreadable.append(f + "（root の外）")
            continue
        text = _read(os.path.join(root, rel))
        if text is None:
            unreadable.append(rel)
            continue
        scanned += 1
        got, seen = 0, set()
        for pri, (name, rx, rep) in enumerate(MUTATORS):
            if got >= max_per_file:
                break
            for ln, line in enumerate(text.splitlines(), 1):
                if got >= max_per_file:
                    break
                if not line.strip() or _COMMENT.match(line) or _SKIP_LINE.match(line) or not line.isascii():
                    continue
                if ln in seen:
                    continue
                new = re.sub(rx, rep, line, count=1)
                if new == line:
                    continue
                cmd = sed_command(rel, line, new)
                if not cmd or not _try_apply(root, rel, cmd):
                    continue
                seen.add(ln)
                got += 1
                res.append({"file": rel, "line": ln, "operator": name, "before": line.strip(), "after": new.strip(),
                            "mutate": cmd, "verified": True})
    return {"root": root, "files": len(files), "scanned": scanned, "unreadable": unreadable, "candidates": res}


def render_mutate(r):
    L = ["# oneshot-spec mutate-candidates — ファイル %d 本中 %d 本を読めた / 候補 %d 件（一時コピーで当てて変わることを確認済み）"
         % (r["files"], r["scanned"], len(r["candidates"])), ""]
    for c in r["candidates"]:
        L.append("- `%s:%d` %s" % (c["file"], c["line"], c["operator"]))
        L.append("  - 前: `%s`" % c["before"])
        L.append("  - 後: `%s`" % c["after"])
        L.append("  - mutate: `%s`" % c["mutate"])
    for u in r["unreadable"]:
        L.append("❓ 読めない: %s" % u)
    L += ["", "候補は **実装前の** ファイルに対するもの。実装で行が変わると当たらなくなる（preflight の mutation が「何も変えなかった」と言う）。",
          "どの 1 手で赤になるべきか（どのテストが落ちるべきか）は利用者が決める。"]
    return "\n".join(L)


# ─────────────────────────────── budget ───────────────────────────────

TOKENS_PER_ATTEMPT = 80000      # 1 周（実装 → 検証）の本体の見込み（仮置き・実走の tokens.used で磨く）
TOKENS_PER_SUBAGENT = 40000     # subagent 1 回の見込み（同上）
UNATTENDED_MARGIN_PCT = 10      # 無人用の常設指示でツール呼び出しと出力が増える分（Opus 5.5 の手引き）・% の整数


def derive_budget(hours, fail_tol, watch_min, subagents):
    minutes = int(math.floor(hours * 60 * 0.8))
    attempts = int(fail_tol)
    raw = attempts * TOKENS_PER_ATTEMPT + subagents * TOKENS_PER_SUBAGENT
    marg = (raw * (100 + UNATTENDED_MARGIN_PCT) + 99) // 100      # 整数で計算（小数の誤差で 1 万ずれない）
    tokens = ((marg + 9999) // 10000) * 10000
    why = [
        "minutes = 期限 %g 時間 × 60 × 0.8 = %d 分（残り 2 割は人が PR を読んで直す時間）" % (hours, minutes),
        "attempts = 失敗が %d 回続いたら見たい → goal-loop --max %d" % (fail_tol, attempts),
        "tokens = (周 %d × %d + subagent %d 回 × %d) × (1 + %d%%) を 1 万で切り上げ = %d"
        % (attempts, TOKENS_PER_ATTEMPT, subagents, TOKENS_PER_SUBAGENT, UNATTENDED_MARGIN_PCT, tokens),
        "  係数（1 周・1 subagent の見込み・無人指示の上乗せ）は仮置き。実走の tokens.used で磨く（設計 §7-2）",
    ]
    if watch_min >= minutes:
        why.append("途中経過: %d 分ごとに見たい ≥ 無人区間 %d 分 → 終わるまで途中は見ない（PR で見る）" % (watch_min, minutes))
    else:
        why.append("途中経過: %d 分ごと → 最大 %d 回見る（state.json と各周 1 行の進捗）" % (watch_min, int(math.ceil(minutes / float(watch_min)))))
    return {"attempts": attempts, "minutes": minutes, "tokens": tokens, "why": why}


def render_budget(b):
    L = ["# oneshot-spec budget — 提示（確定ではない）", ""]
    L += ["- " + w if not w.startswith("  ") else "  " + w.strip() for w in b["why"]]
    L += ["", "利用者が値を確かめ、よければ oneshot.yaml に書く（**確定するのは利用者**。機械は既定値を置かない）:", "",
          "```yaml", "budget:", "  attempts: %d" % b["attempts"], "  minutes: %d" % b["minutes"], "  tokens: %d" % b["tokens"], "```"]
    return "\n".join(L)


# ─────────────────────────────── init（oneshot.yaml の下書き）───────────────────────────────

def _q(s):
    return "'" + str(s).replace("'", "''") + "'"


def draft_yaml(det):
    c = det["candidates"]
    L = ["# oneshot.yaml — 下書き（oneshot-spec.py init）。Phase −1 で利用者と詰めて埋める。",
         "# 候補は oneshot-spec.py detect の出力から 1 つずつ入れた。**どれを使うかは利用者が決める。**",
         "# 空のまま（goal・mutate・scope・budget）では spec も preflight も通らない = 正しい挙動。",
         "goal: ''                     # 1 文。何ができれば終わりか",
         "dod:"]
    picked = 0
    for kind in O.KINDS:
        cands = c.get(kind) or []
        if not cands:
            L.append("  # kind: %s の候補は検出できなかった — 利用者と決める" % kind)
            continue
        first = cands[0]
        picked += 1
        L.append("  - kind: %s" % kind)
        L.append("    cmd: %s%s" % (_q(first["cmd"]), "   # 要記入" if first.get("要記入") else ""))
        if kind == "new":
            L.append("    count: %s%s" % (_q(first.get("count") or ""), "" if first.get("count") else "   # 実出力で確かめて書く"))
            L.append("    mutate: ''   # 壊せば赤になる 1 手（oneshot-spec.py mutate-candidates --files …）")
        for alt in cands[1:4]:
            L.append("    # 他の候補: %s" % alt["cmd"])
    L += ["scope:",
          "  paths: []                  # 触ってよい範囲（空なら起動しない）",
          "forbid:                      # scope に勝つ。oneshot.yaml / oneshot/ / .claude/ は書かなくても入る",
          "  paths: ['infra/', '.github/', '*.env*', 'oneshot.yaml', 'oneshot/', '.claude/']",
          "  gate_config: []            # DoD が読む baseline / 除外リスト / floor",
          "budget:                      # 既定値なし。問 6 の答えから oneshot-spec.py budget で導いて利用者が確定する",
          "  attempts:",
          "  minutes:",
          "  tokens:",
          "level: 1                     # L1 のみ（2 以上は起動拒否）",
          ""]
    return "\n".join(L), picked


# ─────────────────────────────── spec.md ───────────────────────────────

KIND_JA = {
    "new": "新規の判定 — この変更のために書く検査。実装前は赤・壊せば赤・実行件数 0 なら赤",
    "regression": "回帰 — 今動いているものを壊していないこと（CI の必須チェック）",
    "boundary": "境界 — 触る層に対応するゲート。触ってはいけない所に入っていないこと",
}
NON_GOALS = [
    "merge（PR を開いて止まる。merge は人）",
    "deploy・本番リリース",
    "公開（OSS 公開・ストア提出・告知）",
    "価格・課金の変更",
    "顧客リポへの書き込み",
    "仕様の曖昧さを推測で埋めること（曖昧なら止まって聞く）",
]


def render_spec(c, contract_sha):
    b = c.get("budget") or {}
    L = ["# oneshot 仕様書", "",
         "> 生成: `oneshot-spec.py spec`（%s）。元は `oneshot.yaml`（sha256 `%s`）。" % (O.now_iso(), (contract_sha or "?")[:16]),
         "> **このファイルを直さず、oneshot.yaml を直して作り直す。** 契約が変われば sha256 が変わる。", "",
         "## 目的", "", str(c.get("goal") or "").strip(), "",
         "## 完了条件（全部 exit 0 で完了。自己申告では完了にしない）", "",
         "| # | 種類 | コマンド | 実行件数の取り方 |", "|---|---|---|---|"]
    for i, e in enumerate(c.get("dod") or []):
        L.append("| %d | %s | `%s` | %s |" % (i, KIND_JA.get(e.get("kind"), e.get("kind")), e.get("cmd"),
                                            ("`%s`（0 件・取れない は赤）" % e["count"]) if e.get("count") else "—"))
    L += ["", "## 壊せば赤になる 1 手（測定器が本物である証拠）", "",
          "緑になった後、下の 1 手を 1 本ずつ当てて **赤になること** を確かめ、戻す。緑のままなら測定器が壊れている。", "",
          "| dod | 壊す 1 手 |", "|---|---|"]
    for i, e in enumerate(c.get("dod") or []):
        if e.get("kind") == "new":
            L.append("| #%d | `%s` |" % (i, e.get("mutate")))
    L += ["", "## 触ってよい範囲", ""] + ["- `%s`" % p for p in O.scope_paths(c)]
    own = [p for p in O.forbid_paths(c) if p not in O.DEFAULT_FORBID]
    L += ["", "## 触らない（scope より強い）", "",
          "- 既定（契約と gate の設定自身）: " + " / ".join("`%s`" % p for p in O.DEFAULT_FORBID)]
    if own:
        L.append("- この契約で追加: " + " / ".join("`%s`" % p for p in own))
    gc = O.gate_config(c)
    L.append("- gate の設定（緩めて通す逃げ道を塞ぐ）: " + (" / ".join("`%s`" % p for p in gc) if gc else "なし"))
    m = b.get("minutes")
    L += ["", "## 予算（どれかを超えたら止まって人へ）", "",
          "- 試行: %s 回" % b.get("attempts"),
          "- 時間: %s 分（%s）" % (m, ("%d 時間 %d 分" % (m // 60, m % 60)) if isinstance(m, int) else "?"),
          "- トークン: %s（subagent を含む合計）" % b.get("tokens"),
          "", "## やらないこと（無人区間には入れない）", ""] + ["- " + n for n in NON_GOALS]
    L += ["", "## 承認", "",
          "- [ ] この仕様で無人区間に入ってよい — **「走らせてよい」と言うのは人**。機械はこの欄を埋めない",
          "- 承認した人: ＿＿＿＿　日時: ＿＿＿＿", ""]
    return "\n".join(L)


# ─────────────────────────────── CLI ───────────────────────────────

def cmd_detect(a):
    root = os.path.abspath(a.root) if a.root else O.repo_root()
    r = detect(root)
    print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else render_detect(r))
    if r["total"] == 0:
        if not a.json:
            print("\n❓ 候補 0 件 — 「無い」ではなく「この道具では測れていない」。DoD は利用者と一から決める（exit 2）")
        return 2
    return 0


def cmd_mutate(a):
    root = os.path.abspath(a.root) if a.root else O.repo_root()
    if not a.files:
        print("❗ --files が空（exit 2）", file=sys.stderr)
        return 2
    r = mutate_candidates(root, a.files, a.max)
    print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else render_mutate(r))
    if r["scanned"] == 0:
        return 2
    if not r["candidates"]:
        if not a.json:
            print("\n候補 0 件 — 機械では壊す 1 手を見つけられなかった。**人が書く**（exit 1）")
        return 1
    return 0


def cmd_budget(a):
    missing = [n for n, v in (("--deadline-hours（いつまでに要るか）", a.deadline_hours),
                              ("--fail-tolerance（何回失敗が続いたら見たいか）", a.fail_tolerance),
                              ("--watch-every-min（途中経過を何分ごとに見たいか）", a.watch_every_min),
                              ("--subagents（subagent を何回まで使ってよいか）", a.subagents)) if v is None]
    if missing:
        print("❗ 既定値は置かない（設計 §7-2）。問 6 の答えが足りない: " + " / ".join(missing) + "（exit 2）", file=sys.stderr)
        return 2
    if a.deadline_hours <= 0 or a.fail_tolerance <= 0 or a.watch_every_min <= 0 or a.subagents < 0:
        print("❗ 値が正でない（deadline・fail・watch は正、subagents は 0 以上）（exit 2）", file=sys.stderr)
        return 2
    b = derive_budget(a.deadline_hours, a.fail_tolerance, a.watch_every_min, a.subagents)
    print(json.dumps(dict(b, confirmed=False), ensure_ascii=False, indent=2) if a.json else render_budget(b))
    return 0


def cmd_init(a):
    root = os.path.abspath(a.root) if a.root else O.repo_root()
    p = os.path.join(root, O.CONTRACT)
    if os.path.exists(p):
        print("❗ %s は既にある — 上書きしない（exit 1）。直すなら直接編集して `oneshot-spec.py spec` で確かめる" % O.CONTRACT)
        return 1
    det = detect(root)
    text, picked = draft_yaml(det)
    try:
        c = O.load_yaml(text)
    except O.YamlError as e:
        print("❗ 下書きが自分で読めない（道具のバグ）: %s（exit 2）" % e, file=sys.stderr)
        return 2
    if not isinstance(c, dict):
        print("❗ 下書きが map にならない（道具のバグ・exit 2）", file=sys.stderr)
        return 2
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    probs = O.validate_contract(c)
    print("# oneshot-spec init — %s を書いた（dod の候補 %d 種 / 3 種・残りの問題 %d 件）" % (O.CONTRACT, picked, len(probs)))
    for x in probs:
        print("  - [%s] %s" % (x["level"], x["msg"]))
    print("\n下書き。Phase −1 で利用者と埋める → `oneshot-spec.py spec` で spec.md を作る。")
    if det["total"] == 0:
        print("❓ 候補 0 件 — dod は空（測れていない）。利用者と一から決める")
    return 0


def cmd_spec(a):
    root = os.path.abspath(a.root) if a.root else O.repo_root()
    c, err = O.load_contract(root)
    if c is None:
        print("❗ " + err + "（exit 2）")
        return 2
    probs = O.validate_contract(c)
    if probs:
        rej = [x for x in probs if x["level"] == "reject"]
        print("# oneshot-spec spec — 契約に問題 %d 件（reject %d / Phase −1 へ戻す %d）。spec.md は書かない" % (len(probs), len(rej), len(probs) - len(rej)))
        for x in probs:
            print("  - [%s] %s" % (x["level"], x["msg"]))
        return 2 if rej else 1
    sha = O.sha256_file(os.path.join(root, O.CONTRACT))
    os.makedirs(os.path.join(root, O.ODIR), exist_ok=True)
    out = os.path.join(root, O.ODIR, "spec.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(render_spec(c, sha))
    print("# oneshot-spec spec — %s を書いた（dod %d 本・scope %d パス）。利用者が読んで「走らせてよい」と言ったら preflight へ"
          % (os.path.relpath(out, root), len(c.get("dod") or []), len(O.scope_paths(c))))
    return 0


# ─────────────────────────────── self-test ───────────────────────────────

def _self_test() -> int:
    import contextlib
    import io
    ok = ng = 0

    def check(name, cond, detail=""):
        nonlocal ok, ng
        if cond:
            ok += 1
            print("  ✅ " + name)
        else:
            ng += 1
            print("  ❌ " + name + (("  — " + str(detail)[:300]) if detail else ""))

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

    print("── oneshot-spec self-test ──")
    # count: 実出力の例で runner ごとに当てる（一致しない = 0 件と書かない）
    for runner, (sample, want) in COUNT_SAMPLES.items():
        got = O.count_from_output(COUNT[runner], sample)
        check("count[%s]: 実出力の例から %d 件" % (runner, want), got == want, "got %r" % got)
    check("count[jest]: 失敗を含んでも passed を取る", O.count_from_output(COUNT["jest"], "Tests:       1 failed, 11 passed, 12 total") == 11)
    check("count[xctest]: 入れ子の suite を二重に数えない（All tests の行だけ）", O.count_from_output(COUNT["xctest"], COUNT_SAMPLES["xctest"][0]) == 16)
    check("count[pytest]: テストが走らない出力は None（0 と書かない）", O.count_from_output(COUNT["pytest"], "no tests ran in 0.01s") is None)

    with tempfile.TemporaryDirectory() as td:
        # detect: 何も無い → exit 2
        empty = os.path.join(td, "empty"); os.makedirs(empty)
        rc, out = run(["detect", "--root", empty])
        check("detect: 何も検出できない → exit 2（測れていない）", rc == 2 and "測れていない" in out, out)
        # detect: node（monorepo の functions/）+ Makefile + CI + UI + firestore.rules + harness-check
        repo = os.path.join(td, "repo")
        w(repo, "functions/package.json", json.dumps({"scripts": {"test": "jest --ci", "lint": "eslint .", "build": "tsc"},
                                                      "devDependencies": {"jest": "^29"}}))
        w(repo, "web/package.json", json.dumps({"scripts": {"test": "vitest run", "typecheck": "tsc --noEmit"}}))
        w(repo, "web/pnpm-lock.yaml", "")
        w(repo, "Makefile", "check: lint ios-test\nios-test:\n\txcodebuild test\nX := 1\nlint:\n\techo\n")
        w(repo, ".github/workflows/ci.yml", "jobs:\n  functions:\n    name: functions (lint)\n    steps:\n      - uses: actions/checkout@v4\n      - run: cd functions && npm ci\n")
        w(repo, "web/src/App.tsx", "export const A = () => <button>ok</button>\n")
        w(repo, "firestore.rules", "rules_version = '2';\n")
        w(repo, "scripts/harness-check.sh", "#!/bin/sh\n")
        r = detect(repo)
        news = [c["cmd"] for c in r["candidates"]["new"]]
        check("detect: monorepo の functions/ を cd つきで拾う（jest）", "cd functions && npm test" in news, news)
        check("detect: pnpm の web/ を pnpm で拾う（vitest）", "cd web && pnpm test" in news, news)
        jest = [c for c in r["candidates"]["new"] if c["cmd"] == "cd functions && npm test"][0]
        check("detect: jest の count を付ける", jest.get("count") == COUNT["jest"], jest)
        check("detect: Makefile の *-test は new 候補（count は要記入）", any(c["cmd"] == "make ios-test" and c.get("要記入") for c in r["candidates"]["new"]))
        check("detect: Makefile の := を target と読まない", "X" not in [s for st in r["stacks"] if st["stack"] == "make" for s in st["targets"]])
        regs = [c["cmd"] for c in r["candidates"]["regression"]]
        check("detect: CI の job の run を回帰候補に", any("cd functions && npm ci" in x for x in regs), regs)
        check("detect: typecheck を回帰候補に", "cd web && pnpm run typecheck" in regs, regs)
        bnds = [c["cmd"] for c in r["candidates"]["boundary"]]
        check("detect: UI（tsx）→ a11y ゲートを境界候補に", any("a11y-static-check.py" in x for x in bnds), bnds)
        check("detect: firestore.rules → rules fixture を境界候補に", any("firestore-rules-access" in x for x in bnds))
        check("detect: harness-check.sh → 境界候補に", "bash scripts/harness-check.sh" in bnds)
        rc, out = run(["detect", "--root", repo])
        check("detect: 候補あり → exit 0・分母つきの見出し", rc == 0 and "走査したマーカー" in out and "候補" in out, out[:200])

        # mutate-candidates
        w(repo, "web/src/rule.ts", "// if (a === b) return true\nimport x from 'y'\nexport function ok(a: number, b: number) {\n"
                                   "  if (a === b) return true\n  const s = 'it''s'\n  return a >= b && a < 10\n}\n")
        w(repo, "web/src/plain.ts", "export const NAME = 'plain'\n")
        rc, out = run(["mutate-candidates", "--root", repo, "--files", "web/src/rule.ts", "--json"])
        r = json.loads(out) if rc == 0 else {}
        cands = r.get("candidates") or []
        check("mutate: 候補を出す（exit 0）", rc == 0 and len(cands) >= 1, out[:300])
        check("mutate: コメント行・import 行を壊さない", all(c["line"] not in (1, 2) for c in cands), cands)
        check("mutate: 1 ファイルの上限（既定 3）を守る", len(cands) <= 3)
        check("mutate: 全候補が GNU/BSD 両用の sed -i.bak … && rm -f", all(c["mutate"].startswith("sed -i.bak ") and "&& rm -f " in c["mutate"] for c in cands))
        # 案内どおりに当てると本当に変わる（本物のコピーで確かめる）
        if cands:
            c0 = cands[0]
            clone = os.path.join(td, "clone"); shutil.copytree(repo, clone)
            before = open(os.path.join(clone, c0["file"]), encoding="utf-8").read()
            subprocess.run(c0["mutate"], shell=True, cwd=clone, check=False)
            after = open(os.path.join(clone, c0["file"]), encoding="utf-8").read()
            check("mutate: 提示した 1 手を当てるとファイルが変わる", before != after and c0["after"] in after, c0)
            check("mutate: .bak を残さない", not os.path.exists(os.path.join(clone, c0["file"] + ".bak")))
        # 一重引用符を含む行でも sed が当たる
        q = sed_command("x.ts", "  const s = 'a' === b", "  const s = 'a' !== b")
        w(repo, "x.ts", "  const s = 'a' === b\n")
        check("mutate: 一重引用符を含む行を shell で正しく引用する", _try_apply(repo, "x.ts", q), q)
        rc, out = run(["mutate-candidates", "--root", repo, "--files", "web/src/plain.ts"])
        check("mutate: 壊す手が見つからない → exit 1（人が書く）", rc == 1 and "人が書く" in out, out)
        rc, out = run(["mutate-candidates", "--root", repo, "--files", "nope.ts"])
        check("mutate: 読めるファイルが無い → exit 2", rc == 2, out)

        # budget
        rc, out = run(["budget", "--deadline-hours", "5", "--fail-tolerance", "3", "--watch-every-min", "60", "--subagents", "4", "--json"])
        b = json.loads(out) if rc == 0 else {}
        check("budget: 4 つの答えから導く（minutes = 5h × 0.8）", rc == 0 and b.get("minutes") == 240 and b.get("attempts") == 3, out[:200])
        check("budget: tokens = (3×80k + 4×40k) × 1.1 を 1 万で切り上げ", b.get("tokens") == 440000, b.get("tokens"))
        check("budget: 確定ではないと明示（confirmed=false）", b.get("confirmed") is False)
        rc, out = run(["budget", "--deadline-hours", "5", "--fail-tolerance", "3", "--watch-every-min", "60", "--subagents", "4"])
        check("budget: 人向けの出力に「提示（確定ではない）」", rc == 0 and "確定ではない" in out and "attempts: 3" in out, out[:300])
        rc, out = run(["budget", "--deadline-hours", "5"])
        check("budget: 答えが足りない → exit 2（既定値を置かない）", rc == 2 and "既定値は置かない" in out, out)

        # init → spec（案内どおりに埋めると通る）
        rc, out = run(["init", "--root", repo])
        check("init: oneshot.yaml の下書きを書く（exit 0）", rc == 0 and os.path.exists(os.path.join(repo, "oneshot.yaml")), out)
        c, err = O.load_contract(repo)
        check("init: 下書きを共通部品で読める", c is not None, err)
        probs = O.validate_contract(c or {})
        codes = {x["code"] for x in probs}
        check("init: 下書きのままでは通らない（goal・mutate・budget・scope）", {"goal", "mutate", "budget", "scope"} <= codes, codes)
        rc, out = run(["init", "--root", repo])
        check("init: 既にあれば上書きしない（exit 1）", rc == 1 and "上書きしない" in out)
        rc, out = run(["spec", "--root", repo])
        check("spec: 下書きのまま → spec.md を書かない（scope 空は reject = exit 2）", rc == 2 and not os.path.exists(os.path.join(repo, "oneshot", "spec.md")), out)
        text = open(os.path.join(repo, "oneshot.yaml"), encoding="utf-8").read()
        text = text.replace("goal: ''", "goal: 'ボタンの押し間違いを防ぐ'")
        text = text.replace("mutate: ''", "mutate: %s" % _q(cands[0]["mutate"] if cands else "sed -i.bak 's/a/b/' x && rm -f x.bak"))
        text = text.replace("  paths: []", "  paths: ['web/src/']")
        text = text.replace("  attempts:\n  minutes:\n  tokens:", "  attempts: 3\n  minutes: 240\n  tokens: 440000")
        w(repo, "oneshot.yaml", text)
        c, err = O.load_contract(repo)
        check("spec: 案内どおりに埋めた契約は問題 0", c is not None and O.validate_contract(c) == [], O.validate_contract(c or {}))
        rc, out = run(["spec", "--root", repo])
        sp = os.path.join(repo, "oneshot", "spec.md")
        body = open(sp, encoding="utf-8").read() if os.path.exists(sp) else ""
        check("spec: 埋めた契約 → spec.md を書く（exit 0）", rc == 0 and body, out)
        for sec in ("## 目的", "## 完了条件", "## 壊せば赤になる 1 手", "## 触ってよい範囲", "## 触らない", "## 予算", "## やらないこと", "## 承認"):
            check("spec: 節「%s」がある" % sec, sec in body)
        check("spec: 承認欄は空（機械は埋めない）", "- [ ] この仕様で無人区間に入ってよい" in body and "- [x]" not in body)
        check("spec: 契約の sha256 を記す", (O.sha256_file(os.path.join(repo, "oneshot.yaml")) or "")[:16] in body)
        check("spec: 既定の forbid（契約と gate の設定自身）を書く", "`oneshot.yaml`" in body and "`.claude/`" in body)
        # 問題が phase-1 だけ → exit 1
        w(repo, "oneshot.yaml", text.replace("  attempts: 3", "  attempts:"))
        rc, out = run(["spec", "--root", repo])
        check("spec: budget が空 → exit 1（Phase −1 へ戻す）", rc == 1 and "budget" in out, out)
        w(repo, "oneshot.yaml", text.replace("level: 1", "level: 2"))
        rc, out = run(["spec", "--root", repo])
        check("spec: level 2 → exit 2（reject）", rc == 2 and "reject" in out, out)
        w(repo, "oneshot.yaml", "goal: \"閉じていない\n")
        rc, out = run(["spec", "--root", repo])
        check("spec: 読めない契約 → exit 2", rc == 2, out)

    print("\n検査 %d 件: 合格 %d / 不合格 %d" % (ok + ng, ok, ng))
    if ng:
        return 1
    print("✅ PASS — 候補を出し（count は実出力で当てる・mutate は当てて確かめる）、既定値を置かず、契約が整うまで spec を書かない")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in argv:
        return _self_test()
    ap = argparse.ArgumentParser(prog="oneshot-spec.py", description="/oneshot Phase −1 の機械側（候補を出すだけ）")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("detect"); p.add_argument("--root"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("mutate-candidates"); p.add_argument("--root"); p.add_argument("--files", nargs="+")
    p.add_argument("--max", type=int, default=3); p.add_argument("--json", action="store_true")
    p = sub.add_parser("budget")
    p.add_argument("--deadline-hours", type=float); p.add_argument("--fail-tolerance", type=int)
    p.add_argument("--watch-every-min", type=int); p.add_argument("--subagents", type=int); p.add_argument("--json", action="store_true")
    p = sub.add_parser("init"); p.add_argument("--root")
    p = sub.add_parser("spec"); p.add_argument("--root")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if not a.cmd:
        print(__doc__)
        return 2
    return {"detect": cmd_detect, "mutate-candidates": cmd_mutate, "budget": cmd_budget, "init": cmd_init, "spec": cmd_spec}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
