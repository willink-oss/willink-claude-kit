#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""govern.py — crew の学習資産・ガバナンス監査 CLI（F系共通ツール）.

単一ファイル・サブコマンド式。python3 標準ライブラリのみ。macOS/BSD 互換。
対象欠如は『対象無・観測継続』で exit0（空≠ゼロ件）。各サブ: 人間可読 + --json。

サブコマンド:
  distill          未蒸留ミス（common-mistakes にルール化されていない mistake-log）を列挙
  knowledge-dedup  assets/knowledge/*.md の title 近似重複クラスタを検出
  memory-hygiene   MEMORY.md 索引 × memory/*.md の 孤児/リンク切れ/古い/type欠落
  kpi              自然言語ルール残数・CI required・advisory hook 数を ledger 行で出力
  adr-lint         ADR の 欠番/superseded-cited/dangling参照 を検出
  approval-audit   git log から Level3 相当の痕跡で承認マーカ欠如を列挙（heuristic）
  kit-drift        kit 側 .claude/hooks|rules と crew 側同名ファイルの差分を列挙
  skill-lifecycle  .claude/skills/* の最終更新から N日無更新の dead 候補を出す
  halluc-lesson    自己申告 vs live 実測 乖離系ミスに対応する検証テストの有無を照合

自己テスト:
  govern.py <sub> --self-test   そのサブだけ hermetic 検証（exit0/1）
  govern.py --self-test         全9サブを回し全 pass で exit0
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import os as _os_ph
import sys as _sys_ph
_sys_ph.path.insert(0, _os_ph.path.dirname(_os_ph.path.abspath(__file__)))
import _phroot  # proof-harness: 検査対象ルート解決

OBSERVE = "対象無・観測継続"

# ---------------------------------------------------------------------------
# 低レベルヘルパ
# ---------------------------------------------------------------------------

_JP_RUN = re.compile(r"[぀-ヿ一-鿿]{2,}")
_ASCII_TOK = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{2,}")
_STOP = {
    "the", "and", "for", "with", "that", "this", "から", "こと", "ため",
    "する", "した", "して", "よう", "この", "その", "など", "れる", "られ",
    "とき", "もの", "ない", "です", "ます", "という",
}


def _read(path):
    """UTF-8 でファイルを読む。読めなければ None。"""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _read_head(path, n):
    try:
        out = []
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, ln in enumerate(fh):
                if i >= n:
                    break
                out.append(ln)
        return "".join(out)
    except OSError:
        return None


def _hash(path):
    try:
        h = hashlib.sha1()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _date(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _significant_terms(text):
    """語彙照合用の distinctive term 集合を返す。"""
    terms = set()
    for m in _ASCII_TOK.findall(text or ""):
        terms.add(m.lower())
    for m in _JP_RUN.findall(text or ""):
        terms.add(m)
    return {t for t in terms if t not in _STOP}


def _parse_dated_entries(text):
    """mistake-log の `### YYYY-MM-DD: title` エントリを [{date,title,body}] で返す。

    非日付の `###` セクションはミスとして扱わない（境界としてのみ使用）。
    """
    entries = []
    cur = None
    hdr = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2}):\s*(.*)$")
    for ln in (text or "").split("\n"):
        m = hdr.match(ln)
        if m:
            if cur:
                entries.append(cur)
            cur = {"date": m.group(1), "title": m.group(2).strip(), "body": ""}
        elif cur is not None:
            if ln.startswith("### "):
                entries.append(cur)
                cur = None
            else:
                cur["body"] += ln + "\n"
    if cur:
        entries.append(cur)
    return entries


def _adr_dir_default(root):
    """ADR の置き場所を探す。**リポごとに違う**ので候補順に見る。

    OSS 配布物は特定リポのディレクトリ構成に依存できないので、
    汎用の候補を先に見て、無ければ環境変数 ADR_DIR を使う。
    どれも無ければ最初の候補を返す（呼び出し側が「無い」として扱える）。
    """
    import os as _os
    env = _os.environ.get("ADR_DIR")
    if env:
        return Path(env)
    for c in ("docs/adr", "architecture/adr", "doc/adr", "adr"):
        p = root / c
        if p.is_dir():
            return p
    return root / "docs/adr"


def _first_heading(text):
    for ln in (text or "").split("\n"):
        s = ln.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()
    return ""


def _first_paragraph(text):
    for ln in (text or "").split("\n"):
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith(">") or s.startswith("---"):
            continue
        return s
    return ""


def _norm_title(t):
    """title を近似重複クラスタリング用のキーに正規化。"""
    t = (t or "").strip().lstrip("#").strip()
    t = re.sub(r"[（(][^）)]*[）)]", "", t)                       # 括弧内除去
    t = re.sub(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?", "", t)      # 日付除去
    t = re.sub(r"\d{4}", "", t)                                   # 年号除去
    t = re.sub(r"[\s\-–—_:：、。・/／|｜.]+", "", t)               # 区切り除去
    return t.lower()


def _frontmatter(text):
    """先頭 --- ... --- の frontmatter 本文を返す。無ければ None。"""
    if not text or not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[3:end]


# ---------------------------------------------------------------------------
# レポート組立
# ---------------------------------------------------------------------------


def _finish(sub, findings, notes=None, counts=None):
    status = "violations" if findings else "clean"
    return {
        "subcommand": sub,
        "status": status,
        "findings": findings,
        "notes": notes or [],
        "counts": counts or {},
    }


def _observe(sub, msg, counts=None):
    return {
        "subcommand": sub,
        "status": "observe",
        "findings": [],
        "notes": [msg],
        "counts": counts or {},
    }


def exit_code(report):
    return 1 if report.get("status") == "violations" else 0


def _fmt_finding(f):
    return " ".join(f"{k}={v}" for k, v in f.items())


def emit(report, as_json):
    if as_json:
        if report["subcommand"] == "kpi":
            print(json.dumps(report["ledger"], ensure_ascii=False))
        else:
            print(json.dumps(report, ensure_ascii=False))
        return
    print("# govern {} — {}".format(report["subcommand"], report["status"]))
    for n in report.get("notes", []):
        print("  note: {}".format(n))
    if report["subcommand"] == "kpi":
        led = report["ledger"]
        print("  自然言語ルール: {} bullets / {} files / {} lines".format(
            led["nl_rules"], led["rule_files"], led["nl_rule_lines"]))
        print("  hooks: advisory={} blocking={}".format(
            led["advisory_hooks"], led["blocking_hooks"]))
        print("  CI required checks: {}".format(led["ci_required_checks"]))
        if led["kpi_doc"]:
            for k, v in led["kpi_doc"].items():
                print("  KPI[{}] = {}".format(k, v))
        print("LEDGER " + json.dumps(led, ensure_ascii=False))
        return
    if report["status"] == "observe":
        print("  {}".format(OBSERVE))
    elif not report["findings"]:
        print("  違反なし (clean)")
    else:
        print("  findings: {}".format(len(report["findings"])))
        for f in report["findings"]:
            print("  - " + _fmt_finding(f))
    if report.get("counts"):
        print("  counts: " + json.dumps(report["counts"], ensure_ascii=False))


# ---------------------------------------------------------------------------
# 1. distill
# ---------------------------------------------------------------------------


def compute_distill(archive_path, rules_path, min_overlap=2):
    at = _read(archive_path)
    rt = _read(rules_path)
    if at is None or rt is None:
        return _observe("distill", "{}: archive/rules 不在".format(OBSERVE))
    entries = _parse_dated_entries(at)
    if not entries:
        return _observe("distill", "{}: mistake エントリ無".format(OBSERVE))
    rules_terms = _significant_terms(rt)
    findings = []
    for e in entries:
        date = e["date"]
        mterms = _significant_terms(e["title"] + " " + e["body"])
        overlap = mterms & rules_terms
        distilled = (date in rt) or (len(overlap) >= min_overlap)
        if not distilled:
            findings.append({
                "date": date,
                "title": e["title"][:80],
                "matched_terms": sorted(overlap)[:5],
                "reason": "common-mistakes に対応ルール無し",
            })
    return _finish("distill", findings,
                   counts={"mistakes": len(entries), "undistilled": len(findings)})


# ---------------------------------------------------------------------------
# 2. knowledge-dedup
# ---------------------------------------------------------------------------


def _dedup_allow_keys(knowledge_dir):
    """レビュー済み時系列 series の allow-list を読む（.dedup-series-allow）。

    リサーチ続報（5月版→6月版 等）は title キーが同じでも意図的な時系列であり
    重複でない（2026-07-06 監査: 4 クラスタ全てが series と判定）。運用者/責任者 が
    レビューして series と確定したキーを 1 行 1 キーで記録し、以後 violation に
    数えない。# 始まりはコメント。新規クラスタは引き続き検出される。
    """
    allow = set()
    p = Path(knowledge_dir) / ".dedup-series-allow"
    t = _read(p)
    if t:
        for line in t.split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                allow.add(line)
    return allow


def compute_knowledge_dedup(knowledge_dir):
    d = Path(knowledge_dir)
    if not d.is_dir():
        return _observe("knowledge-dedup", "{}: knowledge dir 不在 {}".format(OBSERVE, d))
    files = sorted(d.glob("*.md"))
    if not files:
        return _observe("knowledge-dedup", "{}: knowledge md 無".format(OBSERVE))
    allow = _dedup_allow_keys(knowledge_dir)
    buckets = {}
    para_terms = {}
    for p in files:
        head = _read_head(p, 40) or ""
        title = _first_heading(head)
        para_terms[p.name] = _significant_terms(_first_paragraph(head))
        key = _norm_title(title)
        if key:
            buckets.setdefault(key, []).append(p.name)
    findings = []
    allowed_skipped = 0
    for key, members in sorted(buckets.items()):
        if len(members) >= 2:
            if key in allow:
                allowed_skipped += 1
                continue
            # 代表ペアの先頭段落 Jaccard を近似類似として付す
            sims = []
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = para_terms[members[i]], para_terms[members[j]]
                    if a or b:
                        sims.append(len(a & b) / max(1, len(a | b)))
            findings.append({
                "key": key,
                "members": sorted(members),
                "count": len(members),
                "para_similarity": round(max(sims), 2) if sims else 0.0,
            })
    return _finish("knowledge-dedup", findings,
                   counts={"files": len(files), "clusters": len(findings),
                           "allowed_series": allowed_skipped})


# ---------------------------------------------------------------------------
# 3. memory-hygiene
# ---------------------------------------------------------------------------


def compute_memory_hygiene(memory_dir, stale_days=90, now=None):
    now = now or time.time()
    d = Path(os.path.expanduser(str(memory_dir)))
    if not d.is_dir():
        return _observe("memory-hygiene", "{}: memory dir 不在 {}".format(OBSERVE, d))
    idx_text = _read(d / "MEMORY.md")
    linked = set()
    notes = []
    if idx_text is None:
        notes.append("MEMORY.md 不在: orphan/dangling 判定をスキップ")
    else:
        for m in re.finditer(r"\]\(([^)]+\.md)\)", idx_text):
            linked.add(os.path.basename(m.group(1)))
    files = sorted(p for p in d.glob("*.md") if p.name != "MEMORY.md")
    present = {p.name for p in files}
    findings = []
    if idx_text is not None:
        for name in sorted(linked):
            if name != "MEMORY.md" and name not in present:
                findings.append({"type": "dangling", "file": name})
    for p in files:
        name = p.name
        if idx_text is not None and name not in linked:
            findings.append({"type": "orphan", "file": name})
        try:
            age = (now - p.stat().st_mtime) / 86400.0
        except OSError:
            age = 0.0
        if age > stale_days:
            findings.append({"type": "stale", "file": name, "age_days": round(age, 1)})
        fm = _frontmatter(_read(p) or "")
        if fm is None or not re.search(r"(?m)^type:\s*\S", fm):
            findings.append({"type": "missing-type", "file": name})
    return _finish("memory-hygiene", findings, notes=notes,
                   counts={"files": len(files), "linked": len(linked),
                           "issues": len(findings)})


# ---------------------------------------------------------------------------
# 4. kpi
# ---------------------------------------------------------------------------


def _parse_kpi_table(text):
    out = {}
    in_kpi = False
    for ln in (text or "").split("\n"):
        s = ln.strip()
        if s.startswith("## "):
            in_kpi = "KPI" in s
            continue
        if in_kpi and s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 2 or not cells[0]:
                continue
            key = cells[0]
            if key == "KPI" or set(key) <= set("-: "):
                continue
            out[key] = cells[1]
    return out


def _gh_required_checks(timeout=8):
    """gh 経由で全社の required check 数を best-effort 取得。失敗時 None。"""
    if not shutil.which("gh"):
        return None
    try:
        repo = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True, text=True, timeout=timeout)
        if repo.returncode != 0 or not repo.stdout.strip():
            return None
        nwo = repo.stdout.strip()
        br = subprocess.run(
            ["gh", "api", "repos/{}".format(nwo), "-q", ".default_branch"],
            capture_output=True, text=True, timeout=timeout)
        branch = br.stdout.strip() if br.returncode == 0 else "main"
        prot = subprocess.run(
            ["gh", "api",
             "repos/{}/branches/{}/protection/required_status_checks".format(nwo, branch)],
            capture_output=True, text=True, timeout=timeout)
        if prot.returncode != 0:
            return 0
        data = json.loads(prot.stdout or "{}")
        return len(data.get("contexts", []) or data.get("checks", []) or [])
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def compute_kpi(rules_dir, harness_doc, hooks_dir, use_gh=False, now=None):
    now = now or time.time()
    rules_dir, harness_doc, hooks_dir = Path(rules_dir), Path(harness_doc), Path(hooks_dir)
    have_rules = rules_dir.is_dir()
    have_doc = harness_doc.is_file()
    if not have_rules and not have_doc:
        return _observe("kpi", "{}: rules_dir/harness_doc 不在".format(OBSERVE))
    notes = []
    nl_rules = nl_lines = rule_files = 0
    if have_rules:
        for p in sorted(rules_dir.glob("*.md")):
            t = _read(p) or ""
            rule_files += 1
            nl_lines += t.count("\n") + 1
            nl_rules += len(re.findall(r"(?m)^-\s+\*\*", t))
    kpi_doc = _parse_kpi_table(_read(harness_doc) or "") if have_doc else {}
    adv = blk = 0
    if hooks_dir.is_dir():
        for p in sorted(hooks_dir.glob("*.sh")):
            t = _read(p) or ""
            if re.search(r"(?m)exit\s+2", t):
                blk += 1
            else:
                adv += 1
    ci_required = None
    if use_gh:
        ci_required = _gh_required_checks()
        if ci_required is None:
            notes.append("CI required: gh 取得不可・観測継続")
    else:
        notes.append("CI required: gh 未実行（--use-gh で計測）")
    ledger = {
        "date": _date(now),
        "nl_rules": nl_rules,
        "nl_rule_lines": nl_lines,
        "rule_files": rule_files,
        "advisory_hooks": adv,
        "blocking_hooks": blk,
        "ci_required_checks": ci_required,
        "kpi_doc": kpi_doc,
    }
    return {
        "subcommand": "kpi",
        "status": "measured",
        "findings": [],
        "notes": notes,
        "counts": {"nl_rules": nl_rules, "advisory_hooks": adv, "blocking_hooks": blk},
        "ledger": ledger,
    }


# ---------------------------------------------------------------------------
# 5. adr-lint
# ---------------------------------------------------------------------------

_ADR_NUM = re.compile(r"^(?:adr[-_]?)?0*(\d{1,3})\b", re.I)
_ADR_REF = re.compile(r"ADR[-\s]?0*(\d{1,3})")
_SUPERSEDED = re.compile(r"superseded|廃止|置換|置き換え|旧版", re.I)


def _adr_own_num(path):
    m = _ADR_NUM.match(os.path.basename(str(path)))
    return int(m.group(1)) if m else None


def _adr_status_text(text):
    """ステータス/Status 見出し節のみ返す（無ければ先頭 10 行）。

    superseded 判定を status 文脈に限定するため。本文中の「〜を廃止」「〜に置換」
    （変更内容の記述）を ADR 自体の廃止と誤検出しない（2026-07-06 監査で、
    2 本の ADR が本文キーワードにより false positive になった実例の再発防止）。
    """
    m = re.search(r"^#+.*(ステータス|status).*$", text, re.I | re.M)
    if not m:
        return "\n".join(text.split("\n")[:10])
    rest = text[m.end():]
    nxt = re.search(r"^#+ ", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def compute_adr_lint(adr_dir, doc_paths):
    d = Path(adr_dir)
    if not d.is_dir():
        return _observe("adr-lint", "{}: adr dir 不在 {}".format(OBSERVE, d))
    nums = {}
    for p in sorted(d.glob("*.md")):
        n = _adr_own_num(p)
        if n is not None:
            nums[n] = p
    if not nums:
        return _observe("adr-lint", "{}: ADR 番号ファイル無".format(OBSERVE))
    findings = []
    lo, hi = min(nums), max(nums)
    for n in range(lo, hi + 1):
        if n not in nums:
            findings.append({"type": "gap", "adr": "ADR-{:03d}".format(n)})
    superseded = set()
    for n, p in nums.items():
        if _SUPERSEDED.search(_adr_status_text(_read(p) or "")):
            superseded.add(n)
    seen = set()
    for dp in doc_paths:
        t = _read(dp)
        if t is None:
            continue
        base = os.path.basename(str(dp))
        own = _adr_own_num(dp) if str(dp).startswith(str(d)) else None
        doc_is_superseded = own in superseded
        for m in _ADR_REF.finditer(t):
            rn = int(m.group(1))
            if rn == own:
                continue
            if rn not in nums:
                k = ("dangling-ref", rn, base)
                if k not in seen:
                    seen.add(k)
                    findings.append({"type": "dangling-ref",
                                     "adr": "ADR-{:03d}".format(rn), "in": base})
            elif rn in superseded and not doc_is_superseded:
                k = ("superseded-cited", rn, base)
                if k not in seen:
                    seen.add(k)
                    findings.append({"type": "superseded-cited",
                                     "adr": "ADR-{:03d}".format(rn), "in": base})
    counts = {
        "adr_count": len(nums),
        "gaps": sum(1 for f in findings if f["type"] == "gap"),
        "dangling_refs": sum(1 for f in findings if f["type"] == "dangling-ref"),
        "superseded_cited": sum(1 for f in findings if f["type"] == "superseded-cited"),
    }
    return _finish("adr-lint", findings, counts=counts)


# ---------------------------------------------------------------------------
# 6. approval-audit
# ---------------------------------------------------------------------------

_L3_SIGNAL = re.compile(
    r"\bsecret\b|\btoken\b|rotat|\bdns\b|カスタムドメイン|本番|production|"
    r"drop\s+table|\breset\b|destroy|破壊|価格|pricing|\bprice\b|"
    r"branch\s*protection|required\s*check|force[-\s]?push|\biam\b|"
    r"pause\s+project|監視停止|revoke", re.I)
_APPROVAL_MARK = re.compile(
    r"\[CEO承認\]|CEO承認|責任者 approval|L3\s*approved|承認済|事前承認|approved-by", re.I)


def _gather_commit_messages(root, days, log_file):
    if log_file:
        t = _read(log_file)
        if t is None:
            return None
        if "\x1e" in t:
            return [c.strip() for c in t.split("\x1e") if c.strip()]
        return [ln for ln in t.split("\n") if ln.strip()]
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "log",
             "--since={} days ago".format(days), "--format=%s%n%b%x1e"],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return None
        return [c.strip() for c in r.stdout.split("\x1e") if c.strip()]
    except (subprocess.SubprocessError, OSError):
        return None


def compute_approval_audit(messages):
    if messages is None:
        return _observe("approval-audit", "{}: git log/log-file 取得不可".format(OBSERVE))
    if not messages:
        return _observe("approval-audit", "{}: 対象コミット無".format(OBSERVE))
    findings = []
    for msg in messages:
        m = _L3_SIGNAL.search(msg)
        if m and not _APPROVAL_MARK.search(msg):
            findings.append({"signal": m.group(0), "message": msg.strip()[:100]})
    return _finish("approval-audit", findings,
                   counts={"commits": len(messages), "flagged": len(findings)})


# ---------------------------------------------------------------------------
# 7. kit-drift
# ---------------------------------------------------------------------------


def compute_kit_drift(kit_dir, crew_root, subdirs=(".claude/hooks", ".claude/rules")):
    kd = Path(os.path.expanduser(str(kit_dir)))
    if not kd.is_dir():
        return _observe("kit-drift", "{}: kit dir 不在 {}".format(OBSERVE, kd))
    present = [s for s in subdirs if (kd / s).is_dir()]
    if not present:
        return _observe("kit-drift",
                        "{}: kit に .claude/hooks|rules 無 {}".format(OBSERVE, kd))
    crew = Path(crew_root)
    findings = []
    for s in present:
        for kp in sorted((kd / s).glob("*")):
            if not kp.is_file():
                continue
            cp = crew / s / kp.name
            rel = "{}/{}".format(s, kp.name)
            if not cp.is_file():
                findings.append({"type": "missing-in-crew", "file": rel})
            elif _hash(kp) != _hash(cp):
                findings.append({"type": "drift", "file": rel})
    return _finish("kit-drift", findings,
                   counts={"scanned_dirs": len(present), "issues": len(findings)})


# ---------------------------------------------------------------------------
# 8. skill-lifecycle
# ---------------------------------------------------------------------------


def _last_update_ts(root, target):
    """target の最終更新 epoch。git log 優先・不能ならファイル mtime にフォールバック。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%ct", "--", str(target)],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip())
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    latest = None
    tp = Path(target)
    paths = [tp] + list(tp.rglob("*")) if tp.is_dir() else [tp]
    for p in paths:
        try:
            if p.is_file() or p.is_dir():
                mt = p.stat().st_mtime
                latest = mt if latest is None else max(latest, mt)
        except OSError:
            continue
    return latest


def compute_skill_lifecycle(skills_dir, root, max_age_days=90, now=None):
    now = now or time.time()
    d = Path(skills_dir)
    if not d.is_dir():
        return _observe("skill-lifecycle", "{}: skills dir 不在 {}".format(OBSERVE, d))
    skills = [p for p in sorted(d.iterdir()) if p.is_dir()]
    if not skills:
        return _observe("skill-lifecycle", "{}: skill 無".format(OBSERVE))
    findings = []
    for sp in skills:
        ts = _last_update_ts(root, sp)
        if ts is None:
            continue
        age = (now - ts) / 86400.0
        if age > max_age_days:
            findings.append({"skill": sp.name, "age_days": round(age, 1),
                             "last_update": _date(ts)})
    return _finish("skill-lifecycle", findings,
                   counts={"skills": len(skills), "dead": len(findings)})


# ---------------------------------------------------------------------------
# 9. halluc-lesson
# ---------------------------------------------------------------------------

HALLUC_MARKERS = [
    "未実測", "文書ベース", "推定", "自己申告", "unverified", "誤認", "誤記",
    "転載", "サイレント", "near-miss", "実測なし", "偽陰性", "裏取り", "断定",
]


def compute_halluc_lesson(archive_path, coverage_paths, min_overlap=2):
    at = _read(archive_path)
    if at is None:
        return _observe("halluc-lesson", "{}: archive 不在".format(OBSERVE))
    entries = _parse_dated_entries(at)
    halluc = [e for e in entries
              if any(k in (e["title"] + e["body"]) for k in HALLUC_MARKERS)]
    if not halluc:
        return _observe("halluc-lesson", "{}: 該当ミス無".format(OBSERVE))
    corpus = ""
    for cp in coverage_paths:
        t = _read(cp)
        if t:
            corpus += "\n" + t
    corpus_terms = _significant_terms(corpus)
    findings = []
    for e in halluc:
        mterms = _significant_terms(e["title"] + " " + e["body"])
        overlap = mterms & corpus_terms
        if len(overlap) < min_overlap:
            findings.append({
                "date": e["date"],
                "title": e["title"][:80],
                "matched_terms": sorted(overlap)[:5],
                "reason": "対応する検証テスト未検出",
            })
    return _finish("halluc-lesson", findings,
                   counts={"halluc": len(halluc), "uncovered": len(findings)})


# ---------------------------------------------------------------------------
# パス解決（実行時デフォルト）
# ---------------------------------------------------------------------------

def _default_memory_dir(root=None):
    """Claude Code の project memory ディレクトリを実行環境から導出する。

    固定文字列で持つと配布先で必ず外れる（`~/.claude/projects/` 配下の
    ディレクトリ名は cwd の絶対パスの `/` を `-` にしたもの）。
    `PH_MEMORY_DIR` で明示上書き可・`--memory-dir` はさらに優先。
    """
    env = os.environ.get("PH_MEMORY_DIR")
    if env:
        return os.path.expanduser(env)
    base = os.path.abspath(root or os.getcwd())
    return os.path.expanduser(
        os.path.join("~/.claude/projects", base.replace(os.sep, "-"), "memory"))


DEFAULT_MEMORY = _default_memory_dir()
# 上流 kit（drift 比較の相手）。配布先ごとに異なるため env で差し替え可。
DEFAULT_KIT = os.path.expanduser(
    os.environ.get("PH_UPSTREAM_KIT_DIR", "~/GitHub/willink-claude-kit"))


def _default_root():
    return Path(_phroot.target_root())


def _governance_docs(root, adr_dir):
    docs = list(Path(adr_dir).glob("*.md")) if Path(adr_dir).is_dir() else []
    for rel in ("CLAUDE.md", "company-profile.md"):
        p = root / rel
        if p.is_file():
            docs.append(p)
    for sub in ("standards", ".claude/rules"):
        d = root / sub
        if d.is_dir():
            docs.extend(sorted(d.glob("*.md")))
    return docs


def _coverage_paths(root):
    paths = []
    for sub, pat in ((".claude/hooks", "*.sh"), ("scripts", "*.sh"), ("scripts", "*.py")):
        d = root / sub
        if d.is_dir():
            paths.extend(sorted(d.glob(pat)))
    return paths


# ---------------------------------------------------------------------------
# 自己テスト（hermetic fixture）
# ---------------------------------------------------------------------------


def _mktemp():
    return tempfile.mkdtemp(prefix="govern-st-")


def _w(path, text):
    Path(path).write_text(text, encoding="utf-8")
    return path


def st_distill():
    d = _mktemp()
    try:
        arch = os.path.join(d, "arch.md")
        rules = os.path.join(d, "rules.md")
        _w(arch,
           "# log\n\n"
           "### 2026-05-02: amplify deploy で本番 regression\n本文 amplify deploy 検証\n\n"
           "### 2026-09-09: xyzzy 未知トピック frobnicate\n全く無関係な内容\n")
        _w(rules, "- **merged deploy 確認** — amplify deploy を実測する 2026-05-02\n")
        rep = compute_distill(arch, rules, min_overlap=2)
        a = (rep["status"] == "violations"
             and any(f["date"] == "2026-09-09" for f in rep["findings"])
             and not any(f["date"] == "2026-05-02" for f in rep["findings"]))
        rep2 = compute_distill(os.path.join(d, "none.md"), rules)
        b = rep2["status"] == "observe"
        return ("distill", a and b, "detect={} observe={}".format(a, b))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_knowledge_dedup():
    d = _mktemp()
    try:
        kd = os.path.join(d, "k")
        os.makedirs(kd)
        _w(os.path.join(kd, "a.md"), "# Foo Bar Report 2026-04-01\n\npara alpha shared\n")
        _w(os.path.join(kd, "b.md"), "# Foo Bar Report (2026-05-02)\n\npara alpha shared\n")
        _w(os.path.join(kd, "c.md"), "# Totally Unique Thing\n\npara gamma\n")
        rep = compute_knowledge_dedup(kd)
        a = (rep["status"] == "violations"
             and any(set(f["members"]) == {"a.md", "b.md"} for f in rep["findings"]))
        empty = os.path.join(d, "empty")
        os.makedirs(empty)
        b = compute_knowledge_dedup(empty)["status"] == "observe"
        return ("knowledge-dedup", a and b, "detect={} observe={}".format(a, b))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_memory_hygiene():
    d = _mktemp()
    try:
        md = os.path.join(d, "mem")
        os.makedirs(md)
        _w(os.path.join(md, "MEMORY.md"),
           "# idx\n- [present.md](present.md) x\n- [gone.md](gone.md) y\n")
        _w(os.path.join(md, "present.md"), "---\nname: p\ntype: feedback\n---\nbody\n")
        _w(os.path.join(md, "orphan.md"), "---\nname: o\n---\nbody\n")
        stale = os.path.join(md, "stale.md")
        _w(stale, "---\nname: s\ntype: project\n---\nbody\n")
        old = time.time() - 100 * 86400
        os.utime(stale, (old, old))
        rep = compute_memory_hygiene(md, stale_days=90)
        t = {(f["type"], f["file"]) for f in rep["findings"]}
        a = (rep["status"] == "violations"
             and ("dangling", "gone.md") in t
             and ("orphan", "orphan.md") in t
             and ("missing-type", "orphan.md") in t
             and ("stale", "stale.md") in t)
        b = compute_memory_hygiene(os.path.join(d, "nope"))["status"] == "observe"
        return ("memory-hygiene", a and b, "detect={} observe={}".format(a, b))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_kpi():
    d = _mktemp()
    try:
        root = os.path.join(d, "root")
        rdir = os.path.join(root, ".claude", "rules")
        os.makedirs(rdir)
        _w(os.path.join(rdir, "x.md"), "- **ルールA** — 説明\n- **ルールB** — 説明\n")
        sdir = os.path.join(root, "standards")
        os.makedirs(sdir)
        doc = os.path.join(sdir, "harness-engineering.md")
        _w(doc, "# h\n## KPI\n| KPI | 基準値 | 方向 |\n|---|---|---|\n"
                "| 自然言語ルール残数 | 23 / 34 | 減 |\n| CI required check 数 | 0 | 増 |\n")
        hooks = os.path.join(root, ".claude", "hooks")
        os.makedirs(hooks)
        _w(os.path.join(hooks, "block.sh"), "#!/bin/bash\nexit 2\n")
        _w(os.path.join(hooks, "adv.sh"), "#!/bin/bash\nexit 0\n")
        rep = compute_kpi(rdir, doc, hooks, use_gh=False)
        led = rep["ledger"]
        a = (led["nl_rules"] == 2 and led["blocking_hooks"] == 1
             and led["advisory_hooks"] == 1 and bool(led["kpi_doc"]))
        rep2 = compute_kpi(os.path.join(d, "no", "rules"),
                           os.path.join(d, "no", "doc.md"),
                           os.path.join(d, "no", "hooks"), use_gh=False)
        b = rep2["status"] == "observe"
        return ("kpi", a and b, "measure={} observe={}".format(a, b))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_adr_lint():
    d = _mktemp()
    try:
        ad = os.path.join(d, "adr")
        os.makedirs(ad)
        _w(os.path.join(ad, "001-first.md"),
           "# ADR-001\n## ステータス\n採択\n本文 references ADR-002 and ADR-009\n")  # oss-portable-ok: ADR 文書を検査する機能の test fixture
        _w(os.path.join(ad, "002-second.md"),
           "# ADR-002\n## ステータス\nsuperseded by ADR-004\n")  # oss-portable-ok: ADR 文書を検査する機能の test fixture
        _w(os.path.join(ad, "004-fourth.md"), "# ADR-004\n採択\n")  # oss-portable-ok: ADR 文書を検査する機能の test fixture
        docs = [os.path.join(ad, "001-first.md"),
                os.path.join(ad, "002-second.md"),
                os.path.join(ad, "004-fourth.md")]
        rep = compute_adr_lint(ad, docs)
        t = {(f["type"], f.get("adr")) for f in rep["findings"]}
        a = (rep["status"] == "violations"
             and ("gap", "ADR-003") in t  # oss-portable-ok: ADR 文書を検査する機能の test fixture
             and ("dangling-ref", "ADR-009") in t  # oss-portable-ok: ADR 文書を検査する機能の test fixture
             and ("superseded-cited", "ADR-002") in t)  # oss-portable-ok: ADR 文書を検査する機能の test fixture
        b = compute_adr_lint(os.path.join(d, "noadr"), [])["status"] == "observe"
        return ("adr-lint", a and b, "detect={} observe={}".format(a, b))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_approval_audit():
    msgs = [
        "ops: rotate secret token for CI",
        "feat: 価格改定 [CEO承認] 済み",
        "docs: update readme wording",
    ]
    rep = compute_approval_audit(msgs)
    a = (rep["status"] == "violations" and len(rep["findings"]) == 1
         and "secret" in rep["findings"][0]["message"].lower())
    b = compute_approval_audit([])["status"] == "observe"
    c = compute_approval_audit(None)["status"] == "observe"
    return ("approval-audit", a and b and c,
            "detect={} observe_empty={} observe_none={}".format(a, b, c))


def st_kit_drift():
    d = _mktemp()
    try:
        kit = os.path.join(d, "kit")
        crew = os.path.join(d, "crew")
        kh = os.path.join(kit, ".claude", "hooks")
        ch = os.path.join(crew, ".claude", "hooks")
        os.makedirs(kh)
        os.makedirs(ch)
        _w(os.path.join(kh, "same.sh"), "A\n")
        _w(os.path.join(ch, "same.sh"), "A\n")
        _w(os.path.join(kh, "drift.sh"), "KIT-NEW\n")
        _w(os.path.join(ch, "drift.sh"), "CREW-OLD\n")
        _w(os.path.join(kh, "only.sh"), "X\n")
        rep = compute_kit_drift(kit, crew)
        t = {(f["type"], f["file"]) for f in rep["findings"]}
        a = (rep["status"] == "violations"
             and ("drift", ".claude/hooks/drift.sh") in t
             and ("missing-in-crew", ".claude/hooks/only.sh") in t
             and not any(f["file"] == ".claude/hooks/same.sh" for f in rep["findings"]))
        b = compute_kit_drift(os.path.join(d, "nokit"), crew)["status"] == "observe"
        return ("kit-drift", a and b, "detect={} observe={}".format(a, b))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_skill_lifecycle():
    d = _mktemp()
    try:
        sk = os.path.join(d, "skills")
        dead = os.path.join(sk, "dead")
        fresh = os.path.join(sk, "fresh")
        os.makedirs(dead)
        os.makedirs(fresh)
        pf = _w(os.path.join(dead, "SKILL.md"), "---\nname: dead\n---\n")
        _w(os.path.join(fresh, "SKILL.md"), "---\nname: fresh\n---\n")
        old = time.time() - 200 * 86400
        os.utime(pf, (old, old))
        os.utime(dead, (old, old))
        rep = compute_skill_lifecycle(sk, d, max_age_days=90)
        a = (rep["status"] == "violations"
             and any(f["skill"] == "dead" for f in rep["findings"])
             and not any(f["skill"] == "fresh" for f in rep["findings"]))
        b = compute_skill_lifecycle(os.path.join(d, "noskills"), d)["status"] == "observe"
        return ("skill-lifecycle", a and b, "detect={} observe={}".format(a, b))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_halluc_lesson():
    d = _mktemp()
    try:
        arch = os.path.join(d, "arch.md")
        _w(arch,
           "# log\n\n"
           "### 2026-05-01: 文書ベース推定で statusverify を怠り誤認\n"
           "本文 statusverify guard 未実測\n\n"
           "### 2026-08-08: 自己申告で quuxwidget frobnicator を未実測のまま誤記\n"
           "無関係 lorem\n")
        cov = _w(os.path.join(d, "test-hooks.sh"),
                 "#!/bin/bash\n# statusverify guard test\ncheck statusverify guard block pass\n")
        rep = compute_halluc_lesson(arch, [cov], min_overlap=2)
        a = (rep["status"] == "violations"
             and any(f["date"] == "2026-08-08" for f in rep["findings"])
             and not any(f["date"] == "2026-05-01" for f in rep["findings"]))
        b = compute_halluc_lesson(os.path.join(d, "none.md"), [cov])["status"] == "observe"
        return ("halluc-lesson", a and b, "detect={} observe={}".format(a, b))
    finally:
        shutil.rmtree(d, ignore_errors=True)


NAME_TO_ST = {
    "distill": st_distill,
    "knowledge-dedup": st_knowledge_dedup,
    "memory-hygiene": st_memory_hygiene,
    "kpi": st_kpi,
    "adr-lint": st_adr_lint,
    "approval-audit": st_approval_audit,
    "kit-drift": st_kit_drift,
    "skill-lifecycle": st_skill_lifecycle,
    "halluc-lesson": st_halluc_lesson,
}
ALL_ST = [st_distill, st_knowledge_dedup, st_memory_hygiene, st_kpi, st_adr_lint,
          st_approval_audit, st_kit_drift, st_skill_lifecycle, st_halluc_lesson]


def run_selftests(fns):
    results = []
    for fn in fns:
        try:
            name, passed, detail = fn()
        except Exception as e:  # noqa: BLE001 — self-test must never crash the harness
            name, passed, detail = getattr(fn, "__name__", "?"), False, "EXC {!r}".format(e)
        results.append((name, passed))
        print("[{}] {}: {}".format("PASS" if passed else "FAIL", name, detail))
    ok = all(p for _, p in results)
    print("self-test: {}/{} passed".format(sum(1 for _, p in results if p), len(results)))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _dispatch(args):
    root = Path(args.root).resolve()
    sub = args.sub
    if sub == "distill":
        rep = compute_distill(root / "assets/knowledge/mistake-log-archive.md",
                              root / ".claude/rules/common-mistakes.md",
                              args.min_overlap)
    elif sub == "knowledge-dedup":
        kdir = Path(args.knowledge_dir) if args.knowledge_dir else root / "assets/knowledge"
        rep = compute_knowledge_dedup(kdir)
    elif sub == "memory-hygiene":
        mdir = args.memory_dir or DEFAULT_MEMORY
        rep = compute_memory_hygiene(mdir, args.stale_days)
    elif sub == "kpi":
        rep = compute_kpi(root / ".claude/rules", root / "standards/harness-engineering.md",
                          root / ".claude/hooks", use_gh=args.use_gh)
    elif sub == "adr-lint":
        # 既定は汎用のパス。置き場所が違うリポは --adr-dir か ADR_DIR で指す。
        # （置き場所が候補と違うリポは、自分側で --adr-dir か ADR_DIR を指定する）
        adir = Path(args.adr_dir) if args.adr_dir else _adr_dir_default(root)
        rep = compute_adr_lint(adir, _governance_docs(root, adir))
    elif sub == "approval-audit":
        rep = compute_approval_audit(_gather_commit_messages(root, args.days, args.log_file))
    elif sub == "kit-drift":
        rep = compute_kit_drift(args.kit_dir or DEFAULT_KIT, root)
    elif sub == "skill-lifecycle":
        sdir = Path(args.skills_dir) if args.skills_dir else root / ".claude/skills"
        rep = compute_skill_lifecycle(sdir, root, args.max_age_days)
    elif sub == "halluc-lesson":
        rep = compute_halluc_lesson(root / "assets/knowledge/mistake-log-archive.md",
                                    _coverage_paths(root))
    else:
        raise ValueError("unknown subcommand: {}".format(sub))
    emit(rep, args.json)
    return exit_code(rep)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="govern.py",
        description="crew 学習資産・ガバナンス監査 CLI（F系共通ツール）")
    parser.add_argument("--self-test", action="store_true",
                        help="全9サブの hermetic 自己テストを実行")
    sub = parser.add_subparsers(dest="sub")

    def common(p):
        p.add_argument("--root", default=str(_default_root()), help="crew リポジトリルート")
        p.add_argument("--json", action="store_true", help="JSON 出力")
        p.add_argument("--self-test", action="store_true", help="このサブのみ自己テスト")

    p = sub.add_parser("distill", help="未蒸留ミスを列挙")
    common(p)
    p.add_argument("--min-overlap", type=int, default=2)

    p = sub.add_parser("knowledge-dedup", help="ナレッジ近似重複クラスタ検出")
    common(p)
    p.add_argument("--knowledge-dir", default=None)

    p = sub.add_parser("memory-hygiene", help="メモリ索引の衛生検査")
    common(p)
    p.add_argument("--memory-dir", default=None)
    p.add_argument("--stale-days", type=int, default=90)

    p = sub.add_parser("kpi", help="ハーネス KPI 計測 ledger")
    common(p)
    p.add_argument("--use-gh", action="store_true", help="gh で CI required check を計測")

    p = sub.add_parser("adr-lint", help="ADR 欠番/superseded/dangling 検査")
    common(p)
    p.add_argument("--adr-dir", default=None)

    p = sub.add_parser("approval-audit", help="Level3 承認マーカ欠如の監査")
    common(p)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--log-file", default=None, help="commit message ソース（テスト用）")

    p = sub.add_parser("kit-drift", help="kit ↔ crew ハーネス drift 検査")
    common(p)
    p.add_argument("--kit-dir", default=None)

    p = sub.add_parser("skill-lifecycle", help="dead skill 候補検出")
    common(p)
    p.add_argument("--skills-dir", default=None)
    p.add_argument("--max-age-days", type=int, default=90)

    p = sub.add_parser("halluc-lesson", help="自己申告乖離ミスの検証テスト照合")
    common(p)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "self_test", False):
        if args.sub is None:
            return run_selftests(ALL_ST)
        return run_selftests([NAME_TO_ST[args.sub]])
    if args.sub is None:
        parser.print_help()
        return 2
    return _dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
