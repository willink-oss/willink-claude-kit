#!/usr/bin/env python3
"""finding.py — consumer で出た「ハーネスの課題」を記録し、正本へ持ち帰る台帳エンジン
（単一ファイル・標準ライブラリのみ・Python 3.9+）

なぜ在るか:
  ハーネスは consumer（実案件）で使われて初めて壊れ方が分かる。だが作業中に気づいた
  「ゲートの誤検知」「見逃し」「標準の抜け」は、その場で書き留めないと消える。
  書き留めた先が consumer の中に閉じていると正本に還流しない。
  このエンジンは **記録（consumer）→ 持ち帰り（正本の台帳へ PR）→ triage → 昇格** の
  最初の 2 段を機械化し、台帳の整合を決定論で検査する。

consumer 側（作業中に 1 行ずつ）:
  python3 <engines>/finding.py add --kind false-positive --component pre-commit-shell-lint.sh \\
      --summary "..." --evidence "<コマンド> → exit N" [--stage ios] [--severity block]
  python3 <engines>/finding.py list [--status open] [--json]
  python3 <engines>/finding.py stats [--json]
  python3 <engines>/finding.py check [--check]                 台帳の整合（schema / id 重複 / URL 禁止）
  python3 <engines>/finding.py sync [--harness <正本の clone>] [--apply | --push]
      --harness が無ければ .claude/settings.json の marketplace（source=git）の url から正本を
      一時 clone する（consumer の Claude が clone 無しで --push まで回せる・2026-09-17）
  python3 <engines>/finding.py pull [--harness <正本の clone>] [--apply]
      正本側の triage（status / resolution）を consumer の同 id へ写す（sync は片方向だった）

正本側:
  python3 scripts/finding.py check --file findings/consumer-findings.jsonl --check
  python3 scripts/finding.py triage --file findings/consumer-findings.jsonl \\
      --id <consumer>/<id> --status fixed --pr 26
  python3 scripts/finding.py --self-test                hermetic（tmp のみ・実源に触れない）

台帳の 1 行（JSON Lines）:
  id            f-YYYY-MM-DD-NNN（consumer 内で一意。正本では consumer/id が鍵）
  consumer      コードネーム（org/repo のパスや URL は書けない）
  at            UTC ISO-8601
  kind          false-positive | missed | gap | friction | standards | bug-pattern | docs
  component     どのゲート / skill / engine / 標準の節か（例: pre-commit-shell-lint.sh, install.sh, §8）
  summary       1 行・200 字以内
  evidence      コマンドと exit code、または観測できた事実。false-positive / missed / bug-pattern では必須
  stage         design | api | ios | web | ci | ops（任意）
  severity      block | warn | info（既定 warn）
  status        open | triaged | fixed | promoted | wontfix
  resolution    {pr, promoted_to, note}（正本側の triage で入る）
  harness_commit  記録時の .claude/willink-kit/SOURCE.md の commit（取れなければ「不明」）
  synced_at     正本へ持ち帰った UTC 日時（consumer 側だけ）。**ある行は正本の main に無くても再度持ち帰らない**
  pulled_at     正本の triage を pull で写した UTC 日時（consumer 側だけ）

Exit（check）: 0 = 違反なし / 1 = 違反あり（--check 時） / 2 = 台帳が無い（--allow-missing で 0）
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

KINDS = ("false-positive", "missed", "gap", "friction", "standards", "bug-pattern", "docs")
STATUSES = ("open", "triaged", "fixed", "promoted", "wontfix")
SEVERITIES = ("block", "warn", "info")
STAGES = ("design", "api", "ios", "web", "ci", "ops")
EVIDENCE_REQUIRED = {"false-positive", "missed", "bug-pattern"}
ID_RE = re.compile(r"^f-\d{4}-\d{2}-\d{2}-\d{3}$")
URL_RE = re.compile(r"https?://", re.I)
REPO_PATH_RE = re.compile(r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git\b|\bgithub\.com\b", re.I)
CRED_RE = re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|sbp_[a-f0-9]{20,}")
SUMMARY_MAX = 200
HARNESS_LEDGER = os.path.join("findings", "consumer-findings.jsonl")
CONSUMER_LEDGER = os.path.join(".claude", "harness-findings.jsonl")
SOURCE_MD = os.path.join(".claude", "willink-kit", "SOURCE.md")


# ───────────────────────── 基本 ─────────────────────────

def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_toplevel(cwd=None):
    """git の toplevel。無ければ None（推測しない）。"""
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def resolve_root(opts):
    root = getattr(opts, "root", None) or os.environ.get("PH_TARGET_ROOT")
    if root:
        return os.path.abspath(root)
    return git_toplevel() or os.getcwd()


def resolve_file(opts, root):
    """--file > PH_FINDINGS_FILE > 正本の台帳（在れば） > consumer の台帳。"""
    if getattr(opts, "file", None):
        return os.path.abspath(opts.file)
    if os.environ.get("PH_FINDINGS_FILE"):
        return os.path.abspath(os.environ["PH_FINDINGS_FILE"])
    h = os.path.join(root, HARNESS_LEDGER)
    if os.path.isfile(h):
        return h
    return os.path.join(root, CONSUMER_LEDGER)


def read_ledger(path):
    """(rows, parse_errors)。rows は (lineno, dict)。壊れた行は errors に回し、読める行は活かす。"""
    rows, errors = [], []
    if not os.path.isfile(path):
        return rows, errors
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except ValueError as e:
                errors.append((i, "JSON として読めない: {}".format(e)))
                continue
            if not isinstance(obj, dict):
                errors.append((i, "オブジェクトでない"))
                continue
            rows.append((i, obj))
    return rows, errors


def write_ledger(path, objs):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for o in objs:
            f.write(json.dumps(o, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


def append_ledger(path, objs):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for o in objs:
            f.write(json.dumps(o, ensure_ascii=False, sort_keys=True) + "\n")


def key_of(o):
    return "{}/{}".format(o.get("consumer", "?"), o.get("id", "?"))


def harness_commit_from_source(root):
    """consumer の SOURCE.md から commit を読む。無ければ「不明」（空欄にしない）。"""
    p = os.path.join(root, SOURCE_MD)
    if not os.path.isfile(p):
        return "不明"
    with open(p, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\|\s*commit\s*\|\s*`?([0-9a-f]{40}|不明[^|`]*)`?\s*\|", line.strip())
            if m:
                return m.group(1)
    return "不明"


# ───────────────────────── 検査 ─────────────────────────

def validate(o):
    """1 行の違反を list で返す（空なら OK）。"""
    v = []
    for k in ("id", "consumer", "at", "kind", "component", "summary", "status"):
        if not str(o.get(k, "")).strip():
            v.append("{} が空".format(k))
    if o.get("id") and not ID_RE.match(str(o["id"])):
        v.append("id の形式が f-YYYY-MM-DD-NNN でない: {}".format(o["id"]))
    c = str(o.get("consumer", ""))
    if "/" in c or URL_RE.search(c) or " " in c:
        v.append("consumer はコードネームだけ（/・空白・URL 不可）: {}".format(c))
    if o.get("kind") not in KINDS:
        v.append("kind が語彙外: {}".format(o.get("kind")))
    if o.get("status") not in STATUSES:
        v.append("status が語彙外: {}".format(o.get("status")))
    if o.get("severity", "warn") not in SEVERITIES:
        v.append("severity が語彙外: {}".format(o.get("severity")))
    if o.get("stage") is not None and o.get("stage") not in STAGES:
        v.append("stage が語彙外: {}".format(o.get("stage")))
    s = str(o.get("summary", ""))
    if len(s) > SUMMARY_MAX:
        v.append("summary が {} 字（上限 {}）".format(len(s), SUMMARY_MAX))
    if "\n" in s:
        v.append("summary に改行")
    if o.get("kind") in EVIDENCE_REQUIRED and not str(o.get("evidence", "")).strip():
        v.append("kind={} は evidence 必須（コマンドと exit code）".format(o.get("kind")))
    for k in ("summary", "evidence", "expected", "actual", "component"):
        t = str(o.get(k, "") or "")
        if URL_RE.search(t):
            v.append("{} に URL（台帳にリンクを置かない・場所が漏れる）".format(k))
        if REPO_PATH_RE.search(t):
            v.append("{} にリポジトリのパス（org/repo は書かない）".format(k))
        if CRED_RE.search(t):
            v.append("{} に認証情報らしき文字列".format(k))
    st = o.get("status")
    res = o.get("resolution") or {}
    if st in ("fixed", "promoted") and not (res.get("pr") or res.get("promoted_to")):
        v.append("status={} は resolution.pr か promoted_to が要る".format(st))
    return v


def check_ledger(path, allow_missing=False):
    """dict: {exists, total, parse_errors, violations[], dup_keys[]}"""
    rows, perr = read_ledger(path)
    out = {"file": path, "exists": os.path.isfile(path), "total": len(rows),
           "parse_errors": [{"line": i, "issue": m} for i, m in perr],
           "violations": [], "dup_keys": []}
    seen = {}
    for i, o in rows:
        k = key_of(o)
        if k in seen:
            out["dup_keys"].append({"key": k, "lines": [seen[k], i]})
        else:
            seen[k] = i
        for issue in validate(o):
            out["violations"].append({"line": i, "key": k, "issue": issue})
    out["n_issues"] = len(out["violations"]) + len(out["parse_errors"]) + len(out["dup_keys"])
    return out


def cmd_check(opts):
    root = resolve_root(opts)
    path = resolve_file(opts, root)
    r = check_ledger(path)
    if opts.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        if not r["exists"]:
            print("❗ 台帳が無い: {}（0 件ではなく未作成）".format(path))
        else:
            print("[finding check] {}".format(path))
            print("  走査 {} 行 / 読めない {} / id 重複 {} / 違反 {}".format(
                r["total"], len(r["parse_errors"]), len(r["dup_keys"]), len(r["violations"])))
            for e in r["parse_errors"]:
                print("  ❌ L{}: {}".format(e["line"], e["issue"]))
            for d in r["dup_keys"]:
                print("  ❌ 重複 {} (L{} / L{})".format(d["key"], *d["lines"]))
            for v in r["violations"]:
                print("  ❌ L{} {}: {}".format(v["line"], v["key"], v["issue"]))
            if r["n_issues"] == 0:
                print("  ✅ 違反なし（{} 行を検査）".format(r["total"]) if r["total"] else "  ℹ️  0 行（ファイルは存在）")
    if not r["exists"]:
        return 0 if opts.allow_missing else 2
    if opts.check and r["n_issues"] > 0:
        return 1
    return 0


# ───────────────────────── add / list / stats ─────────────────────────

def next_id(rows, day):
    prefix = "f-{}-".format(day)
    n = 0
    for _, o in rows:
        i = str(o.get("id", ""))
        if i.startswith(prefix):
            try:
                n = max(n, int(i[len(prefix):]))
            except ValueError:
                pass
    return "{}{:03d}".format(prefix, n + 1)


def cmd_add(opts):
    root = resolve_root(opts)
    path = resolve_file(opts, root)
    rows, perr = read_ledger(path)
    if perr:
        print("❗ 台帳に読めない行が {} 件ある。先に check で直す: {}".format(len(perr), path), file=sys.stderr)
        return 1
    consumer = opts.consumer or os.environ.get("PH_CONSUMER") or os.path.basename(root.rstrip(os.sep))
    at = now_utc()
    o = {
        "id": next_id(rows, at[:10]),
        "consumer": consumer,
        "at": at,
        "kind": opts.kind,
        "component": opts.component,
        "summary": opts.summary.strip(),
        "evidence": (opts.evidence or "").strip(),
        "severity": opts.severity,
        "status": "open",
        "harness_commit": harness_commit_from_source(root),
        "synced_at": None,
    }
    if opts.stage:
        o["stage"] = opts.stage
    if opts.expected:
        o["expected"] = opts.expected.strip()
    if opts.actual:
        o["actual"] = opts.actual.strip()
    issues = validate(o)
    if issues:
        print("❌ 記録しない（違反 {} 件）:".format(len(issues)), file=sys.stderr)
        for i in issues:
            print("   - " + i, file=sys.stderr)
        return 1
    append_ledger(path, [o])
    print("✅ 記録した {}  → {}（台帳 {} 行）".format(key_of(o), path, len(rows) + 1))
    return 0


def cmd_list(opts):
    root = resolve_root(opts)
    path = resolve_file(opts, root)
    rows, _ = read_ledger(path)
    objs = [o for _, o in rows if not opts.status or o.get("status") == opts.status]
    if opts.json:
        print(json.dumps(objs, ensure_ascii=False, indent=2))
        return 0
    print("[finding list] {}  {} 行中 {} 件{}".format(
        path, len(rows), len(objs), "（status={}）".format(opts.status) if opts.status else ""))
    for o in objs:
        print("  {:<9} {:<14} {:<28} {}".format(o.get("status", "?"), o.get("kind", "?"),
                                              str(o.get("component", "?"))[:28], o.get("summary", "")))
        # synced の印は consumer の台帳にだけ意味がある（正本の行は synced_at 自体を持たない）
        mark = ("synced" if o.get("synced_at") else "unsynced") if "synced_at" in o else ""
        print("           {}  {}".format(key_of(o), mark).rstrip())
    return 0


def tally(objs, field):
    d = {}
    for o in objs:
        k = str(o.get(field, "-"))
        d[k] = d.get(k, 0) + 1
    return dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))


def cmd_stats(opts):
    root = resolve_root(opts)
    path = resolve_file(opts, root)
    rows, perr = read_ledger(path)
    objs = [o for _, o in rows]
    r = {"file": path, "total": len(objs), "parse_errors": len(perr),
         "by_status": tally(objs, "status"), "by_kind": tally(objs, "kind"),
         "by_consumer": tally(objs, "consumer"), "by_component": tally(objs, "component"),
         "unsynced": sum(1 for o in objs if not o.get("synced_at"))}
    if opts.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    print("[finding stats] {}".format(path))
    if not objs:
        print("  0 件（{}）".format("ファイルあり" if os.path.isfile(path) else "ファイル無し"))
        return 0
    print("  合計 {} 件 / 未同期 {} / 読めない行 {}".format(r["total"], r["unsynced"], r["parse_errors"]))
    for name, d in (("status", r["by_status"]), ("kind", r["by_kind"]),
                    ("consumer", r["by_consumer"]), ("component", r["by_component"])):
        print("  {}: ".format(name) + ", ".join("{} {}".format(k, v) for k, v in d.items()))
    return 0


# ───────────────────────── sync / triage ─────────────────────────

def harness_url(root):
    """正本の git URL。PH_HARNESS_URL → .claude/settings.json / settings.local.json の
    extraKnownMarketplaces（source=git）。consumer は plugin を取るためにこれを必ず持っている。"""
    env = os.environ.get("PH_HARNESS_URL")
    if env:
        return env
    found = []
    for name in ("settings.json", "settings.local.json"):
        p = os.path.join(root, ".claude", name)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        mk = d.get("extraKnownMarketplaces")
        for mname, m in (mk.items() if isinstance(mk, dict) else []):
            src = m.get("source") if isinstance(m, dict) else None
            if isinstance(src, dict) and src.get("source") == "git" and src.get("url"):
                found.append((mname, src["url"]))
    for mname, url in found:          # 名前に harness を含むものを優先
        if "harness" in mname:
            return url
    return found[0][1] if found else None


def clone_harness(url):
    """正本を一時ディレクトリへ shallow clone する。(path, error)。"""
    d = tempfile.mkdtemp(prefix="harness-clone-")
    r = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "main", url, d],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return None, "git clone が exit {}: {}".format(r.returncode, r.stderr.strip()[:300])
    return d, None


def strip_consumer_only(o):
    o = dict(o)
    o.pop("synced_at", None)
    return o


def sync_plan(consumer_path, harness_path):
    crows, cerr = read_ledger(consumer_path)
    hrows, herr = read_ledger(harness_path)
    have = {key_of(o) for _, o in hrows}
    # synced_at のある行は持ち帰り済み。正本の PR がまだ open で main に無くても新規に数えない
    # （数えると PR が merge されるまで毎回もう 1 本 PR が立つ・2026-09-17）。
    new = [o for _, o in crows if key_of(o) not in have and not o.get("synced_at")]
    in_flight = sum(1 for _, o in crows if key_of(o) not in have and o.get("synced_at"))
    return {"consumer_total": len(crows), "harness_total": len(hrows), "new": new,
            "already": len(crows) - len(new), "in_flight": in_flight,
            "consumer_errors": cerr, "harness_errors": herr}


def append_to_harness(harness_path, plan):
    """正本の台帳へ追記する（consumer 側は触らない）。"""
    append_ledger(harness_path, [strip_consumer_only(o) for o in plan["new"]])


def mark_synced(consumer_path, plan):
    """consumer 側に synced_at を入れる。**正本へ届いた後**に呼ぶ（--apply は追記直後、--push は push 成功後）。
    push 前に入れると、push が落ちた行が synced_at 付きのまま main に無く、以後の sync が新規に数えず
    永久に持ち帰られない（2026-09-17 レビュー指摘）。"""
    crows, _ = read_ledger(consumer_path)
    keys = {key_of(o) for o in plan["new"]}
    ts = now_utc()
    objs = []
    for _, o in crows:
        if key_of(o) in keys:
            o = dict(o)
            o["synced_at"] = ts
        objs.append(o)
    write_ledger(consumer_path, objs)


def apply_sync(consumer_path, harness_path, plan):
    """--apply: 正本の clone へ追記し、consumer 側に synced_at を入れる。"""
    append_to_harness(harness_path, plan)
    mark_synced(consumer_path, plan)


def cmd_sync(opts):
    root = resolve_root(opts)
    consumer_path = resolve_file(opts, root)
    given = opts.harness or os.environ.get("PH_HARNESS_ROOT", "")
    tmp_clone = None
    if given:
        harness = os.path.abspath(given)
        if not os.path.isdir(os.path.join(harness, "findings")):
            print("❗ 正本の clone を --harness で指す（findings/ が在ること）: '{}'".format(harness), file=sys.stderr)
            return 2
    else:
        url = harness_url(root)
        if not url:
            print("❗ 正本の在処が分からない: --harness <clone> か、.claude/settings.json の "
                  "extraKnownMarketplaces（source=git の url）か、PH_HARNESS_URL", file=sys.stderr)
            return 2
        if opts.apply:
            print("❗ clone 無しの --apply は意味が無い（一時 clone に書いても消える）。--push を使う", file=sys.stderr)
            return 2
        harness, err = clone_harness(url)
        if err:
            print("❗ 正本を取得できない（❓ 持ち帰り不能・0 件ではない）: {}".format(err), file=sys.stderr)
            return 2
        tmp_clone = harness
        if not os.path.isdir(os.path.join(harness, "findings")):
            # marketplace の url が正本でない（findings/ を持たない別リポ）なら、そこへ台帳を作って PR を立てない
            shutil.rmtree(tmp_clone, ignore_errors=True)
            print("❗ clone した先に findings/ が無い（正本ではない）: {}".format(url), file=sys.stderr)
            return 2
        print("[finding sync] 正本を一時 clone した（{}）".format(url))
    try:
        return _sync_with(opts, consumer_path, harness)
    finally:
        if tmp_clone:
            shutil.rmtree(tmp_clone, ignore_errors=True)


def _sync_with(opts, consumer_path, harness):
    harness_path = os.path.join(harness, HARNESS_LEDGER)
    plan = sync_plan(consumer_path, harness_path)
    if plan["consumer_errors"]:
        print("❗ consumer の台帳に読めない行が {} 件。先に check".format(len(plan["consumer_errors"])), file=sys.stderr)
        return 1
    bad = []
    for o in plan["new"]:
        bad += [(key_of(o), i) for i in validate(o)]
    if bad:
        print("❌ 持ち帰れない（正本の検査で落ちる行がある）:", file=sys.stderr)
        for k, i in bad:
            print("   - {}: {}".format(k, i), file=sys.stderr)
        return 1
    print("[finding sync] consumer {} 件 / 正本 {} 件 / 新規 {} / 既に有り {}{}".format(
        plan["consumer_total"], plan["harness_total"], len(plan["new"]), plan["already"],
        "（うち持ち帰り済みで main 未反映 {} — PR が open なら待つ。PR が merge されず消えたなら"
        " その行の synced_at を消せば再送する）".format(plan["in_flight"]) if plan.get("in_flight") else ""))
    for o in plan["new"]:
        print("  + {}  {}  {}".format(key_of(o), o.get("kind"), o.get("summary")))
    if not plan["new"]:
        print("  持ち帰るものは無い")
        return 0
    if not (opts.apply or opts.push):
        print("  （preview — --apply で正本の clone に追記 / --push で worktree → commit → push → PR）")
        return 0
    if opts.apply:
        apply_sync(consumer_path, harness_path, plan)
        print("  ✅ 追記した → {}（commit と PR は手で）".format(harness_path))
        return 0
    return push_sync(consumer_path, harness, plan)


def push_sync(consumer_path, harness, plan):
    """origin/main から一時 worktree を切り、追記 → commit → push → gh pr create。"""
    consumer = plan["new"][0].get("consumer", "consumer")
    day = now_utc()[:10].replace("-", "")
    branch = "learn/findings-{}-{}".format(consumer, day)
    wt = tempfile.mkdtemp(prefix="findings-wt-")
    steps = [
        ["git", "-C", harness, "fetch", "-q", "origin", "main"],
        ["git", "-C", harness, "worktree", "add", "-q", "-b", branch, wt, "origin/main"],
    ]
    for c in steps:
        r = subprocess.run(c, capture_output=True, text=True, check=False)
        if r.returncode != 0:
            print("❌ {} → exit {}\n{}".format(" ".join(c[3:]), r.returncode, r.stderr.strip()), file=sys.stderr)
            return 1
    try:
        hp = os.path.join(wt, HARNESS_LEDGER)
        plan2 = sync_plan(consumer_path, hp)     # worktree の main を基準に取り直す
        if not plan2["new"]:
            print("  origin/main には既に全て有る（持ち帰るものは無い）")
            return 0
        append_to_harness(hp, plan2)
        msg = "learn(findings): {} から {} 件を持ち帰る\n\n".format(consumer, len(plan2["new"]))
        msg += "\n".join("- {} [{}] {}".format(o["id"], o["kind"], o["summary"]) for o in plan2["new"])
        msg += "\n\n検査: python3 scripts/finding.py check --file findings/consumer-findings.jsonl --check\n"
        for c in (["git", "-C", wt, "add", HARNESS_LEDGER],
                  ["git", "-C", wt, "commit", "-q", "-m", msg],
                  ["git", "-C", wt, "push", "-q", "-u", "origin", branch]):
            r = subprocess.run(c, capture_output=True, text=True, check=False)
            if r.returncode != 0:
                print("❌ {} → exit {}（consumer 側の synced_at は入れていない。次の sync で再送する）\n{}".format(
                    c[3], r.returncode, r.stderr.strip()), file=sys.stderr)
                return 1
        mark_synced(consumer_path, plan2)   # push が origin に届いてから
        r = subprocess.run(["gh", "pr", "create", "--fill", "--head", branch], cwd=wt,
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            print("⚠️  push はしたが gh pr create が exit {}。手で PR を開く（branch: {}）\n{}".format(
                r.returncode, branch, r.stderr.strip()), file=sys.stderr)
            return 1
        print("  ✅ PR を開いた: {}".format(r.stdout.strip()))
        return 0
    finally:
        subprocess.run(["git", "-C", harness, "worktree", "remove", "--force", wt],
                       capture_output=True, text=True, check=False)


PULL_FIELDS = ("status", "resolution")


def pull_plan(consumer_path, harness_path):
    """正本の triage を consumer へ写す計画。(更新する行, 同じ, 正本に無い, 読めない)。"""
    crows, cerr = read_ledger(consumer_path)
    hrows, herr = read_ledger(harness_path)
    hmap = {key_of(o): o for _, o in hrows}
    updates, same, missing = [], 0, 0
    for _, o in crows:
        h = hmap.get(key_of(o))
        if h is None:
            missing += 1
            continue
        diff = {f: h[f] for f in PULL_FIELDS if f in h and h.get(f) != o.get(f)}
        if diff:
            updates.append((key_of(o), o.get("status"), h.get("status"), diff))
        else:
            same += 1
    return {"updates": updates, "same": same, "missing": missing, "consumer_total": len(crows),
            "harness_total": len(hrows), "errors": cerr + herr}


def apply_pull(consumer_path, harness_path, plan):
    hrows, _ = read_ledger(harness_path)
    hmap = {key_of(o): o for _, o in hrows}
    crows, _ = read_ledger(consumer_path)
    keys = {k for k, _s, _t, _d in plan["updates"]}
    ts = now_utc()
    objs = []
    for _, o in crows:
        k = key_of(o)
        if k in keys:
            o = dict(o)
            for f in PULL_FIELDS:
                if f in hmap[k]:
                    o[f] = hmap[k][f]
            o["pulled_at"] = ts
        objs.append(o)
    write_ledger(consumer_path, objs)


def cmd_pull(opts):
    """正本側の triage（status / resolution）を consumer の同 id へ写す。sync は片方向で戻らなかった。"""
    root = resolve_root(opts)
    consumer_path = resolve_file(opts, root)
    if not os.path.isfile(consumer_path):
        print("❗ consumer の台帳が無い: {}（写す先が無い）".format(consumer_path), file=sys.stderr)
        return 2
    given = opts.harness or os.environ.get("PH_HARNESS_ROOT", "")
    tmp_clone = None
    if given:
        harness = os.path.abspath(given)
    else:
        url = harness_url(root)
        if not url:
            print("❗ 正本の在処が分からない: --harness か settings.json の marketplace url か PH_HARNESS_URL", file=sys.stderr)
            return 2
        harness, err = clone_harness(url)
        if err:
            print("❗ 正本を取得できない（❓ 0 件ではない）: {}".format(err), file=sys.stderr)
            return 2
        tmp_clone = harness
    try:
        harness_path = os.path.join(harness, HARNESS_LEDGER)
        if not os.path.isfile(harness_path):
            print("❗ 正本の台帳が無い（url が正本でないか、clone に失敗）: {}".format(harness_path), file=sys.stderr)
            return 2
        plan = pull_plan(consumer_path, harness_path)
        if plan["errors"]:
            print("❗ 読めない行が {} 件。先に check".format(len(plan["errors"])), file=sys.stderr)
            return 1
        print("[finding pull] consumer {} 件 / 正本 {} 件 → 更新 {} / 同じ {} / 正本に無い {}".format(
            plan["consumer_total"], plan["harness_total"], len(plan["updates"]), plan["same"], plan["missing"]))
        for k, s_from, s_to, diff in plan["updates"]:
            print("  ~ {}  {} → {}{}".format(k, s_from, s_to,
                                             "  note: " + str((diff.get("resolution") or {}).get("note", ""))[:60]
                                             if "resolution" in diff else ""))
        if opts.json:
            print(json.dumps({k: v for k, v in plan.items() if k != "errors"}, ensure_ascii=False))
        if not plan["updates"]:
            return 0
        if not opts.apply:
            print("  （preview — --apply で consumer の台帳に書く）")
            return 0
        apply_pull(consumer_path, harness_path, plan)
        print("  ✅ {} 件を書いた → {}".format(len(plan["updates"]), consumer_path))
        return 0
    finally:
        if tmp_clone:
            shutil.rmtree(tmp_clone, ignore_errors=True)


def cmd_triage(opts):
    root = resolve_root(opts)
    path = resolve_file(opts, root)
    rows, perr = read_ledger(path)
    if perr:
        print("❗ 読めない行が {} 件。先に check".format(len(perr)), file=sys.stderr)
        return 1
    hit = None
    objs = []
    for _, o in rows:
        if key_of(o) == opts.id:
            o = dict(o)
            o["status"] = opts.status
            res = dict(o.get("resolution") or {})
            if opts.pr:
                res["pr"] = opts.pr
            if opts.promoted_to:
                res["promoted_to"] = opts.promoted_to
            if opts.note:
                res["note"] = opts.note
            res["at"] = now_utc()
            o["resolution"] = res
            issues = validate(o)
            if issues:
                print("❌ 更新しない: " + "; ".join(issues), file=sys.stderr)
                return 1
            hit = o
        objs.append(o)
    if hit is None:
        print("❗ 見つからない: {}（鍵は consumer/id）".format(opts.id), file=sys.stderr)
        return 1
    write_ledger(path, objs)
    print("✅ {} → status={}".format(opts.id, opts.status))
    return 0


# ───────────────────────── self-test ─────────────────────────

def _run(argv, cwd):
    r = subprocess.run([sys.executable, os.path.abspath(__file__)] + argv, cwd=cwd,
                       capture_output=True, text=True, check=False)
    return r.returncode, r.stdout + r.stderr


def self_test():
    results = []

    def case(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    with tempfile.TemporaryDirectory() as T:
        # consumer: git リポ（basename が consumer 名になる）と SOURCE.md
        c = os.path.join(T, "meguri-test")
        os.makedirs(os.path.join(c, ".claude", "willink-kit"))
        subprocess.run(["git", "init", "-q", c], check=False)
        with open(os.path.join(c, SOURCE_MD), "w", encoding="utf-8") as f:
            f.write("| commit | `{}` |\n".format("a" * 40))
        # 正本: findings/ を持つ git リポ
        h = os.path.join(T, "harness")
        os.makedirs(os.path.join(h, "findings"))
        subprocess.run(["git", "init", "-q", h], check=False)
        hl = os.path.join(h, HARNESS_LEDGER)
        open(hl, "w").close()

        # 1. add → id / consumer / harness_commit
        rc, out = _run(["add", "--kind", "gap", "--component", "install.sh",
                        "--summary", "skill は同名スキップなので更新が届かない"], c)
        case("add が exit 0", rc == 0, out)
        rows, _ = read_ledger(os.path.join(c, CONSUMER_LEDGER))
        o = rows[0][1] if rows else {}
        case("id が f-YYYY-MM-DD-001", ID_RE.match(o.get("id", "")) and o["id"].endswith("-001"), o.get("id"))
        case("consumer が dir 名", o.get("consumer") == "meguri-test", o.get("consumer"))
        case("harness_commit を SOURCE.md から読む", o.get("harness_commit") == "a" * 40, o.get("harness_commit"))
        rc, out = _run(["add", "--kind", "false-positive", "--component", "pre-commit-shell-lint.sh",
                        "--summary", "grep の \\s を落とした", "--evidence", "git commit → exit 1", "--stage", "ios"], c)
        case("2 件目の連番が -002", rc == 0 and read_ledger(os.path.join(c, CONSUMER_LEDGER))[0][1][1]["id"].endswith("-002"), out)

        # 2. block 側: evidence 必須 / URL / 語彙外 は記録しない
        rc, out = _run(["add", "--kind", "missed", "--component", "x", "--summary", "y"], c)
        case("missed で evidence 無しは拒否", rc == 1 and "evidence 必須" in out, out)
        rc, out = _run(["add", "--kind", "docs", "--component", "x", "--summary", "see https://example.invalid/x"], c)
        case("URL 入りは拒否", rc == 1 and "URL" in out, out)
        rc, out = _run(["add", "--kind", "bogus", "--component", "x", "--summary", "y"], c)
        case("kind 語彙外は拒否（argparse）", rc != 0, out)
        rc, out = _run(["add", "--kind", "docs", "--component", "x", "--summary", "y", "--consumer", "org/repo"], c)
        case("consumer に / は拒否", rc == 1 and "consumer" in out, out)

        # 3. check pass / block
        rc, out = _run(["check", "--check"], c)
        case("正しい台帳は check exit 0", rc == 0 and "違反なし" in out, out)
        p = os.path.join(c, CONSUMER_LEDGER)
        with open(p, "a", encoding="utf-8") as f:
            f.write("{not json\n")
            f.write(json.dumps({"id": "f-2026-01-01-001", "consumer": "meguri-test", "at": "x", "kind": "gap",
                                "component": "c", "summary": "s", "status": "open"}) + "\n")
            f.write(json.dumps({"id": "f-2026-01-01-001", "consumer": "meguri-test", "at": "x", "kind": "gap",
                                "component": "c", "summary": "s", "status": "fixed"}) + "\n")
        rc, out = _run(["check", "--check"], c)
        case("壊れた行 + 重複 + fixed に pr 無し → exit 1", rc == 1 and "読めない 1" in out and "重複 1" in out
             and "resolution.pr" in out, out)
        rc, out = _run(["check", "--check", "--json"], c)
        case("--json でも exit 1", rc == 1 and '"n_issues"' in out, out[:200])
        # 直す（壊れた 3 行を落とす）
        rows, _ = read_ledger(p)
        write_ledger(p, [o for _, o in rows if o.get("at") != "x"])
        rc, out = _run(["check", "--check"], c)
        case("直せば exit 0", rc == 0, out)

        # 4. 台帳が無い
        e = os.path.join(T, "empty")
        os.makedirs(e)
        subprocess.run(["git", "init", "-q", e], check=False)
        rc, out = _run(["check"], e)
        case("台帳無しは exit 2（0 件と言わない）", rc == 2 and "未作成" in out, out)
        rc, out = _run(["check", "--allow-missing"], e)
        case("--allow-missing なら exit 0", rc == 0, out)

        # 5. stats / list
        rc, out = _run(["stats"], c)
        case("stats に合計と未同期", rc == 0 and "合計 2 件 / 未同期 2" in out, out)
        rc, out = _run(["list", "--status", "open", "--json"], c)
        case("list --json が 2 件", rc == 0 and len(json.loads(out)) == 2, out[:200])

        # 6. sync: preview → apply → 再 apply で 0
        rc, out = _run(["sync", "--harness", h], c)
        case("sync preview は書かない", rc == 0 and "新規 2" in out and os.path.getsize(hl) == 0, out)
        rc, out = _run(["sync", "--harness", h, "--apply"], c)
        hrows, _ = read_ledger(hl)
        case("--apply で正本に 2 行", rc == 0 and len(hrows) == 2, out)
        case("正本の行に synced_at が無い", all("synced_at" not in o for _, o in hrows))
        crows, _ = read_ledger(p)
        case("consumer 側に synced_at が入る", all(o.get("synced_at") for _, o in crows))
        rc, out = _run(["sync", "--harness", h, "--apply"], c)
        case("再 apply は新規 0", rc == 0 and "新規 0" in out and len(read_ledger(hl)[0]) == 2, out)
        rc, out = _run(["sync", "--harness", os.path.join(T, "nowhere")], c)
        case("findings/ の無い場所は exit 2", rc == 2, out)
        # synced_at のある行は、正本の main にまだ無くても新規に数えない（open な PR に載っている）
        h2 = os.path.join(T, "harness-main-behind")
        os.makedirs(os.path.join(h2, "findings")); open(os.path.join(h2, HARNESS_LEDGER), "w").close()
        rc, out = _run(["sync", "--harness", h2], c)
        case("持ち帰り済み（synced_at あり）の行は main に無くても新規 0・in-flight 2", rc == 0 and "新規 0" in out and "main 未反映 2" in out, out)
        rc, out = _run(["check", "--check"], h)
        case("正本側の check が台帳を自動で見つけ exit 0", rc == 0 and HARNESS_LEDGER in out, out)

        # 7. triage
        k = key_of(hrows[0][1])
        rc, out = _run(["triage", "--id", k, "--status", "fixed"], h)
        case("fixed に pr 無しは拒否", rc == 1, out)
        rc, out = _run(["triage", "--id", k, "--status", "fixed", "--pr", "26"], h)
        hrows2, _ = read_ledger(hl)
        t = [o for _, o in hrows2 if key_of(o) == k][0]
        case("triage で status と pr が入る", rc == 0 and t["status"] == "fixed" and t["resolution"]["pr"] == 26, out)
        rc, out = _run(["triage", "--id", "nobody/f-2000-01-01-001", "--status", "wontfix"], h)
        case("無い鍵は exit 1", rc == 1, out)

        # 8. pull: 正本の triage が consumer へ戻る（sync は片方向だった・2026-09-17）
        rc, out = _run(["pull", "--harness", h], c)
        case("pull preview: 更新 1（fixed）・同じ 1・書かない", rc == 0 and "更新 1 / 同じ 1" in out
             and all(o.get("status") == "open" for _, o in read_ledger(p)[0]), out)
        rc, out = _run(["pull", "--harness", h, "--apply"], c)
        crows, _ = read_ledger(p)
        pulled = [o for _, o in crows if key_of(o) == k][0]
        case("pull --apply で status と resolution が写り pulled_at が入る",
             rc == 0 and pulled["status"] == "fixed" and pulled.get("resolution", {}).get("pr") == 26 and pulled.get("pulled_at"), out)
        rc, out = _run(["check", "--check"], c)
        case("写した後の consumer 台帳が check exit 0", rc == 0, out)
        rc, out = _run(["pull", "--harness", h], c)
        case("再 pull は更新 0", rc == 0 and "更新 0 / 同じ 2" in out, out)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "f-2026-02-02-001", "consumer": "meguri-test", "at": now_utc(), "kind": "docs",
                                "component": "c", "summary": "正本に無い行", "status": "open"}) + "\n")
        rc, out = _run(["pull", "--harness", h], c)
        case("正本に無い行は数えるだけで触らない", rc == 0 and "正本に無い 1" in out, out)
        rc, out = _run(["pull", "--harness", os.path.join(T, "nowhere")], c)
        case("正本の台帳が無ければ exit 2", rc == 2, out)

        # 9. --harness 無し: settings.json の marketplace url（source=git）から正本を引く。
        #    URL の代わりに bare repo（ローカル）を origin にし、gh は偽物で PR 作成を成功させる（hermetic）。
        bare = os.path.join(T, "harness.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=False)
        subprocess.run(["git", "-C", h, "add", "-A"], check=False)
        subprocess.run(["git", "-C", h, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"], check=False)
        subprocess.run(["git", "-C", h, "branch", "-M", "main"], check=False)
        subprocess.run(["git", "-C", h, "remote", "add", "origin", bare], check=False)
        subprocess.run(["git", "-C", h, "push", "-q", "origin", "main"], check=False)
        bare_url = "file://" + bare        # file:// なら --depth 1 が効く（ローカルパスだと無視される）
        with open(os.path.join(c, ".claude", "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"extraKnownMarketplaces": {"my-harness": {"source": {"source": "git", "url": bare_url}}},
                       "enabledPlugins": {"some-plugin@my-harness": True}}, f)
        case("settings.json の marketplace url を見つける", harness_url(c) == bare_url, str(harness_url(c)))
        # push 失敗（origin を一時的に読めなくする）→ consumer の synced_at は入らず、次の sync で再送できる
        os.rename(bare, bare + ".off")
        with open(os.path.join(c, ".claude", "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"extraKnownMarketplaces": {"my-harness": {"source": {"source": "git", "url": bare_url}}}}, f)
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "sync", "--push"], cwd=c,
                           capture_output=True, text=True, check=False)
        case("origin に届かないと exit 2 で『取得できない』（黙って 0 件にしない）", r.returncode == 2 and "取得できない" in r.stdout + r.stderr, r.stdout + r.stderr)
        unsynced_before = [o for _, o in read_ledger(p)[0] if not o.get("synced_at")]
        case("失敗した行は synced_at 無しのまま（再送できる）", len(unsynced_before) == 1, str(len(unsynced_before)))
        os.rename(bare + ".off", bare)
        # clone は通るが push が拒否される（origin の pre-receive が exit 1）→ commit 後でも synced_at は入れない
        hook = os.path.join(bare, "hooks", "pre-receive")
        with open(hook, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho rejected-by-test >&2\nexit 1\n")
        os.chmod(hook, 0o755)
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "sync", "--push"], cwd=c,
                           env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"),
                           capture_output=True, text=True, check=False)
        case("push が拒否されると exit 1 で『再送する』と言う", r.returncode == 1 and "再送する" in r.stdout + r.stderr, r.stdout + r.stderr)
        case("push 拒否後も synced_at は入っていない（次の sync で新規に数える）",
             len([o for _, o in read_ledger(p)[0] if not o.get("synced_at")]) == 1)
        os.remove(hook)
        fake_bin = os.path.join(T, "bin")
        os.makedirs(fake_bin)
        with open(os.path.join(fake_bin, "gh"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho https://example.invalid/pr/1\n")
        os.chmod(os.path.join(fake_bin, "gh"), 0o755)
        env = dict(os.environ, PATH=fake_bin + os.pathsep + os.environ.get("PATH", ""),
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "sync", "--push"], cwd=c, env=env,
                           capture_output=True, text=True, check=False)
        out = r.stdout + r.stderr
        case("--harness 無しの sync --push が settings の url（file://・shallow）から clone して push まで通る",
             r.returncode == 0 and "一時 clone" in out and "PR を開いた" in out, out)
        case("push 成功後に consumer の synced_at が入る", all(o.get("synced_at") for _, o in read_ledger(p)[0]))
        br = subprocess.run(["git", "-C", bare, "branch", "--list", "learn/findings-*"], capture_output=True, text=True).stdout
        case("origin（bare）に learn/findings-* ブランチが出来ている", "learn/findings-meguri-test-" in br, br)
        pushed = subprocess.run(["git", "-C", bare, "show", br.strip().lstrip("* ").split()[0] + ":" + HARNESS_LEDGER],
                                capture_output=True, text=True).stdout
        case("push された台帳に正本に無かった行が入っている", "f-2026-02-02-001" in pushed, pushed[-200:])
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "sync", "--apply"], cwd=c, env=env,
                           capture_output=True, text=True, check=False)
        case("clone 無しの --apply は exit 2（意味が無いと言う）", r.returncode == 2 and "--push" in r.stdout + r.stderr, r.stdout + r.stderr)
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "pull"], cwd=c, env=env,
                           capture_output=True, text=True, check=False)
        case("--harness 無しの pull も url から clone して読む", r.returncode == 0 and "[finding pull]" in r.stdout, r.stdout + r.stderr)
        with open(os.path.join(c, ".claude", "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"enabledPlugins": {}}, f)
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "sync", "--push"], cwd=c, env=env,
                           capture_output=True, text=True, check=False)
        case("url が見つからなければ exit 2（黙って 0 件にしない）", r.returncode == 2 and "在処" in r.stdout + r.stderr, r.stdout + r.stderr)
        # url が正本でない（findings/ を持たない別リポ）→ そこに台帳を作らない
        other = os.path.join(T, "other.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", other], check=False)
        ow = os.path.join(T, "other-wt"); os.makedirs(ow)
        subprocess.run(["git", "-C", ow, "init", "-q", "-b", "main"], check=False)
        open(os.path.join(ow, "README"), "w").close()
        subprocess.run(["git", "-C", ow, "add", "-A"], check=False)
        subprocess.run(["git", "-C", ow, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"], check=False)
        subprocess.run(["git", "-C", ow, "push", "-q", other, "main"], check=False)
        with open(os.path.join(c, ".claude", "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"extraKnownMarketplaces": {"my-harness": {"source": {"source": "git", "url": "file://" + other}}}}, f)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "f-2026-02-02-002", "consumer": "meguri-test", "at": now_utc(), "kind": "docs",
                                "component": "c", "summary": "別リポへ行くか", "status": "open"}) + "\n")
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "sync", "--push"], cwd=c, env=env,
                           capture_output=True, text=True, check=False)
        case("url の先に findings/ が無ければ exit 2 で止め、台帳を作らない", r.returncode == 2 and "findings/ が無い" in r.stdout + r.stderr, r.stdout + r.stderr)
        br2 = subprocess.run(["git", "-C", other, "branch", "--list"], capture_output=True, text=True).stdout
        case("別リポに learn/findings-* ブランチが出来ていない", "learn/findings" not in br2, br2)

    n_ok = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        print("  {} {}".format("✅" if ok else "❌", name))
        if not ok and detail:
            print("     " + detail.strip().replace("\n", "\n     ")[:600])
    print("\nself-test: {}/{} passed".format(n_ok, len(results)))
    return 0 if n_ok == len(results) else 1


# ───────────────────────── CLI ─────────────────────────

def build_parser():
    p = argparse.ArgumentParser(prog="finding.py", description="ハーネスの課題を記録し正本へ持ち帰る台帳")
    p.add_argument("--self-test", action="store_true", help="hermetic な自己検査")
    sub = p.add_subparsers(dest="cmd")

    def common(sp):
        sp.add_argument("--file", help="台帳のパス（既定: 正本なら findings/consumer-findings.jsonl、consumer なら .claude/harness-findings.jsonl）")
        sp.add_argument("--root", help="リポジトリのルート（既定: git toplevel）")
        sp.add_argument("--json", action="store_true")

    a = sub.add_parser("add", help="1 件記録する"); common(a)
    a.add_argument("--kind", required=True, choices=KINDS)
    a.add_argument("--component", required=True, help="ゲート / skill / engine / 標準の節")
    a.add_argument("--summary", required=True)
    a.add_argument("--evidence", help="コマンドと exit code（false-positive / missed / bug-pattern は必須）")
    a.add_argument("--stage", choices=STAGES)
    a.add_argument("--severity", choices=SEVERITIES, default="warn")
    a.add_argument("--expected"); a.add_argument("--actual")
    a.add_argument("--consumer", help="コードネーム（既定: PH_CONSUMER か dir 名）")

    l = sub.add_parser("list"); common(l); l.add_argument("--status", choices=STATUSES)
    s = sub.add_parser("stats"); common(s)
    c = sub.add_parser("check"); common(c)
    c.add_argument("--check", action="store_true", help="違反があれば exit 1")
    c.add_argument("--allow-missing", action="store_true", help="台帳が無くても exit 0（consumer の CI 向け）")
    y = sub.add_parser("sync", help="正本へ持ち帰る"); common(y)
    y.add_argument("--harness", help="正本の clone（PH_HARNESS_ROOT でも可。無ければ settings.json の marketplace url から一時 clone）")
    y.add_argument("--apply", action="store_true", help="正本の clone に直接追記（commit / PR は手で）")
    y.add_argument("--push", action="store_true", help="一時 worktree → commit → push → gh pr create")
    u = sub.add_parser("pull", help="正本側の triage（status / resolution）を consumer の同 id へ写す"); common(u)
    u.add_argument("--harness", help="正本の clone（無ければ settings.json の marketplace url から一時 clone）")
    u.add_argument("--apply", action="store_true", help="consumer の台帳に書く（既定は preview）")
    t = sub.add_parser("triage", help="正本側で status を更新"); common(t)
    t.add_argument("--id", required=True, help="consumer/id")
    t.add_argument("--status", required=True, choices=STATUSES)
    t.add_argument("--pr", type=int); t.add_argument("--promoted-to"); t.add_argument("--note")
    return p


def main(argv=None):
    p = build_parser()
    opts = p.parse_args(argv)
    if opts.self_test:
        return self_test()
    if not opts.cmd:
        p.print_help()
        return 2
    return {"add": cmd_add, "list": cmd_list, "stats": cmd_stats, "check": cmd_check,
            "sync": cmd_sync, "pull": cmd_pull, "triage": cmd_triage}[opts.cmd](opts)


if __name__ == "__main__":
    sys.exit(main())
