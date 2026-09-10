#!/usr/bin/env python3
# =============================================================
# eval-harness.py — crew 評価（B系）の決定論採点 共通ツール
#
# 背景: crew の成果物（記事 / standup / commit / 研究）とエージェント挙動を
#   「自己申告でなく機械的な指標」で採点する enforcement primitive を提供する。
#   原則 P1（自己申告禁止）の評価側実装。LLM を呼ばず、入力から決定論的に
#   スコア/率/合意を算出する（同じ入力 → 同じ出力）。
#
# 設計原則:
#   - 対象（源）が無い場合は「観測継続」で status=observe → exit 0
#     （データが無いことを「合格」とも「不合格」とも言わない）
#   - 採点系は --min / --max 閾値を渡した時だけ gate（未達で exit 1）。
#     閾値省略時は status=measured（計測のみ・exit 0）
#   - 各サブ 人間可読 + --json。exit 1 は status=fail のみ
#   - self-test は temp fixture で hermetic（実源に触れない・ハードコード成功禁止）
#
# 使い方:
#   eval-harness.py <sub> [--file/--dir/...] [--min/--max/...] [--json]
#   eval-harness.py <sub> --self-test   そのサブだけ hermetic 検証（exit0/1）
#   eval-harness.py --self-test         全11サブを回し全 pass で exit0
#
# 依存: python3 標準ライブラリのみ（BSD/macOS でそのまま動く・grep -P 不使用）。
# =============================================================
import argparse
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import os as _os_ph
import sys as _sys_ph
_sys_ph.path.insert(0, _os_ph.path.dirname(_os_ph.path.abspath(__file__)))
import _phroot  # harness: 検査対象ルート解決

OBSERVE = "対象無・観測継続"

# --- 正規表現（BSD/Python re・Perl エスケープ不使用） ---
DIGIT_RE = re.compile(r"[0-9０-９]")
LINK_RE = re.compile(r"\[[^\]]*\]\(https?://[^)\s]+\)")
URL_RE = re.compile(r"https?://[^\s)]+")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
CIT_MARK_RE = re.compile(r"^\s*(参考|出典|ソース|引用|references?|sources?)", re.IGNORECASE)

# 事実主張のシグナル（数字が無くても主張とみなす語）
STRONG_CLAIM_KW = ["によると", "によれば", "調査", "報告", "実績", "統計", "データ"]

# style: AIっぽい定型句（style-guide.md「AIっぽさの兆候」準拠）
BANNED_PHRASES = [
    "いかがでしたか", "いかがでしょうか", "結論として", "総じて",
    "と言えるでしょう", "お役に立てれば幸い", "参考になれば幸いです",
]
# style: 断定過多（過剰な確信）の語
OVER_ASSERT_KW = [
    "絶対", "必ず", "間違いなく", "に違いない", "確実に", "誰もが",
    "全ての人", "当然", "べきだ", "疑いようがない",
]

# commit-score: 許容 prefix と空虚語
COMMIT_PREFIX_RE = re.compile(r"^([a-z]+)(\([^)]+\))?:\s+(.*)$")
ALLOWED_PREFIX = {
    # conventional commits
    "feat", "fix", "docs", "ops", "chore", "learn", "refactor",
    "test", "perf", "build", "ci", "style", "revert",
    # crew 固有 prefix（git log 実測に基づく）
    "pm", "harness", "research", "config", "comply", "grow", "launch", "design",
}
PLACEHOLDER_DESC = {
    "update", "updated", "更新", "変更", "wip", "fix", "fixes",
    "tmp", "temp", "asdf", "メモ", "修正", "作業",
}
WHY_MARKERS = [
    "ため", "理由", "なぜ", "回避", "防止", "解消", "修正し", "できるよう",
    "ように", "enable", "prevent", "because", "so that", "→", "avoid",
]

KEEP_CHARS_RE = re.compile(r"[^0-9A-Za-z぀-ゟ゠-ヿ一-鿿㐀-䶿]")

# 成功/失敗マーカ（goal-loop 出力・状態）
SUCCESS_TOKENS = ["GOAL MET"]
CAP_TOKENS = ["CAP REACHED"]
JSON_SUCCESS = {"met", "success", "pass", "passed", "done", "ok"}
JSON_CAP = {"cap", "fail", "failed", "timeout", "aborted", "capped"}


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------
def _default_root():
    return Path(_phroot.target_root())


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, OSError):
        return None


def _read_json(path):
    """(data, error) を返す。missing→(None,'missing') / 壊れ→(None,'invalid: ...')"""
    txt = _read_text(path)
    if txt is None:
        return None, "missing"
    try:
        return json.loads(txt), None
    except (ValueError, TypeError) as e:
        return None, "invalid: {}".format(e)


def mk_report(sub, status, score=None, threshold=None,
              metrics=None, findings=None, notes=None, extra=None):
    r = {"subcommand": sub, "status": status}
    if score is not None:
        r["score"] = round(float(score), 4)
    if threshold is not None:
        r["threshold"] = threshold
    r["metrics"] = metrics or {}
    r["findings"] = findings or []
    r["notes"] = notes or []
    if extra:
        r.update(extra)
    return r


def observe(sub, note):
    return mk_report(sub, "observe", notes=[note])


def gate(sub, value, threshold, ok, mode, score=None,
         metrics=None, findings=None, notes=None):
    """閾値ありなら pass/fail、無ければ measured。"""
    if threshold is None:
        status = "measured"
    else:
        status = "pass" if ok else "fail"
    m = dict(metrics or {})
    m.setdefault("value", round(float(value), 4))
    m.setdefault("mode", mode)
    return mk_report(sub, status, score=score, threshold=threshold,
                     metrics=m, findings=findings, notes=notes)


def exit_code(report):
    return 1 if report.get("status") == "fail" else 0


def emit(r, as_json):
    if as_json:
        print(json.dumps(r, ensure_ascii=False))
        return
    print("# eval {} — {}".format(r["subcommand"], r["status"]))
    if "score" in r:
        print("  score: {}".format(r["score"]))
    if r.get("threshold") is not None:
        print("  threshold: {}".format(r["threshold"]))
    for k, v in (r.get("metrics") or {}).items():
        print("  {}: {}".format(k, v))
    for n in r.get("notes", []):
        print("  note: {}".format(n))
    if r["status"] == "observe":
        print("  {}".format(OBSERVE))
    findings = r.get("findings", [])
    if findings:
        print("  findings: {}".format(len(findings)))
        for f in findings:
            print("  - " + (f if isinstance(f, str)
                            else json.dumps(f, ensure_ascii=False)))


# ---------------------------------------------------------------------------
# テキスト解析ヘルパ（claim / citation / bullet / sentence）
# ---------------------------------------------------------------------------
def line_has_url(line):
    return bool(LINK_RE.search(line) or URL_RE.search(line))


def is_claim(line):
    if HEADING_RE.match(line):
        return False
    s = line.strip()
    if not s or CIT_MARK_RE.match(s):
        return False
    body = LIST_RE.sub("", s)
    if len(body) < 4:
        return False
    return bool(DIGIT_RE.search(body)) or any(k in body for k in STRONG_CLAIM_KW)


def split_sections(text):
    """見出し（# ...）ごとにセクション（行リスト）へ分割。"""
    sections, cur = [], []
    for ln in text.splitlines():
        if HEADING_RE.match(ln):
            if cur:
                sections.append(cur)
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        sections.append(cur)
    return sections or [text.splitlines()]


def section_has_citation(sec_lines):
    return any(line_has_url(l) or CIT_MARK_RE.match(l.strip()) for l in sec_lines)


def get_sentences(text):
    # 見出し行を除去し、リンクはテキストへ、URL は落とす
    clean = []
    for ln in text.splitlines():
        if HEADING_RE.match(ln):
            continue
        ln = LINK_RE.sub(lambda m: re.sub(r"\]\(https?://[^)\s]+\)", "",
                                          m.group(0)).lstrip("["), ln)
        ln = URL_RE.sub("", ln)
        clean.append(ln)
    joined = "\n".join(clean)
    parts = re.split(r"[。.!！?？\n]+", joined)
    return [p.strip() for p in parts if len(p.strip()) >= 2]


def is_bullet(line):
    if LIST_RE.match(line):
        return True
    s = line.strip()
    return bool(s) and s[0] in "✅🔁🟡⏳🛑🔴🟢⚠❓📌➡"


def norm_bullet(line):
    return KEEP_CHARS_RE.sub("", line).lower()


def char_bigrams(s):
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def jaccard(a, b):
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict) and "score" in v:
        return _num(v["score"])
    return None


def _to_score_list(data):
    if isinstance(data, dict):
        vals = [_num(v) for v in data.values()]
    elif isinstance(data, list):
        vals = [_num(v) for v in data]
    else:
        return []
    return [v for v in vals if v is not None]


def _to_score_map(data):
    m = {}
    if isinstance(data, dict):
        for k, v in data.items():
            n = _num(v)
            if n is not None:
                m[str(k)] = n
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "name" in item:
                n = _num(item.get("score", item))
                if n is not None:
                    m[str(item["name"])] = n
    return m


# ---------------------------------------------------------------------------
# 1. citation-coverage
# ---------------------------------------------------------------------------
def compute_citation_coverage(path, min_cov):
    text = _read_text(path)
    if text is None:
        return observe("citation-coverage", "file 無（{}）".format(path))
    total = supported = 0
    for sec in split_sections(text):
        cited = section_has_citation(sec)
        for l in sec:
            if is_claim(l):
                total += 1
                if cited or line_has_url(l):
                    supported += 1
    if total == 0:
        return observe("citation-coverage", "主張 0 件")
    cov = supported / total
    metrics = {"claims": total, "supported": supported}
    return gate("citation-coverage", cov, min_cov, cov >= (min_cov or 0),
                "min", score=cov, metrics=metrics)


# ---------------------------------------------------------------------------
# 2. style-score
# ---------------------------------------------------------------------------
def compute_style_score(path, min_score):
    text = _read_text(path)
    if text is None:
        return observe("style-score", "file 無（{}）".format(path))
    sentences = get_sentences(text)
    if not sentences:
        return observe("style-score", "文 0 件")
    banned = sum(text.count(p) for p in BANNED_PHRASES)
    clean = max(0.0, 1.0 - 0.5 * banned)
    lengths = [len(s) for s in sentences]
    stdev = statistics.pstdev(lengths) if len(lengths) > 1 else 0.0
    variance = min(1.0, stdev / 12.0)
    strong = sum(text.count(k) for k in OVER_ASSERT_KW)
    assertion = max(0.0, 1.0 - min(1.0, strong / 3.0))
    score = 0.4 * clean + 0.3 * variance + 0.3 * assertion
    metrics = {
        "sentences": len(sentences), "banned_phrases": banned,
        "len_stdev": round(stdev, 2), "over_assert": strong,
        "clean": round(clean, 3), "variance": round(variance, 3),
        "assertion": round(assertion, 3),
    }
    return gate("style-score", score, min_score, score >= (min_score or 0),
                "min", score=score, metrics=metrics)


# ---------------------------------------------------------------------------
# 3. standup-novelty
# ---------------------------------------------------------------------------
def compute_standup_novelty(cur_path, prev_path, max_dup):
    cur_text = _read_text(cur_path)
    if cur_text is None:
        return observe("standup-novelty", "cur 無（{}）".format(cur_path))
    prev_text = _read_text(prev_path) if prev_path else None
    if prev_text is None:
        return observe("standup-novelty", "prev 無（初版は重複測定不能）")
    cur_bul = [(norm_bullet(l), char_bigrams(norm_bullet(l)))
               for l in cur_text.splitlines() if is_bullet(l) and norm_bullet(l)]
    prev_bul = [(norm_bullet(l), char_bigrams(norm_bullet(l)))
                for l in prev_text.splitlines() if is_bullet(l) and norm_bullet(l)]
    if not cur_bul:
        return observe("standup-novelty", "cur に bullet 0 件")
    dups = []
    for cn, cg in cur_bul:
        hit = any(cn == pn or jaccard(cg, pg) >= 0.8 for pn, pg in prev_bul)
        if hit:
            dups.append(cn)
    dup_rate = len(dups) / len(cur_bul)
    metrics = {"cur_bullets": len(cur_bul), "prev_bullets": len(prev_bul),
               "duplicates": len(dups)}
    ok = max_dup is None or dup_rate <= max_dup
    return gate("standup-novelty", dup_rate, max_dup, ok, "max",
                score=dup_rate, metrics=metrics)


# ---------------------------------------------------------------------------
# 4. commit-score
# ---------------------------------------------------------------------------
def score_commit_msg(msg):
    m = COMMIT_PREFIX_RE.match(msg.strip())
    prefix_ok = bool(m and m.group(1) in ALLOWED_PREFIX)
    desc = m.group(3).strip() if m else msg.strip()
    not_empty = len(desc) >= 6 and desc.lower() not in PLACEHOLDER_DESC \
        and desc not in PLACEHOLDER_DESC
    has_why = len(desc) >= 25 or any(w in desc for w in WHY_MARKERS)
    return (0.34 * prefix_ok + 0.33 * not_empty + 0.33 * has_why,
            prefix_ok, not_empty, has_why)


def compute_commit_score(messages, min_score):
    if messages is None:
        return observe("commit-score", "git 無 / 範囲取得不能")
    msgs = [m for m in messages if m.strip()]
    if not msgs:
        return observe("commit-score", "範囲に commit 無")
    scores, weak = [], []
    for m in msgs:
        sc, p, n, w = score_commit_msg(m)
        scores.append(sc)
        if sc < 0.67:
            weak.append({"msg": m[:60], "prefix": p, "nonempty": n, "why": w})
    score = sum(scores) / len(scores)
    metrics = {"commits": len(msgs), "weak": len(weak)}
    return gate("commit-score", score, min_score, score >= (min_score or 0),
                "min", score=score, metrics=metrics, findings=weak)


def _git_messages(since, root):
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--since", since, "--pretty=%s"],
            capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return None
    if out.returncode != 0:
        return None
    return [m for m in out.stdout.splitlines() if m.strip()]


# ---------------------------------------------------------------------------
# 5. judge-vote
# ---------------------------------------------------------------------------
def compute_judge_vote(votes_path, agree):
    data, err = _read_json(votes_path)
    if err:
        return observe("judge-vote", "votes {}（{}）".format(err, votes_path))
    if not isinstance(data, list):
        return observe("judge-vote", "votes は list でない")
    votes = [v for v in data if isinstance(v, dict) and "verdict" in v]
    if not votes:
        return observe("judge-vote", "有効 vote 0 件")
    counts = Counter(str(v["verdict"]) for v in votes)
    majority, mc = counts.most_common(1)[0]
    agreement = mc / len(votes)
    scores = [float(v["score"]) for v in votes if _num(v.get("score")) is not None]
    mean = sum(scores) / len(scores) if scores else None
    var = statistics.pvariance(scores) if len(scores) > 1 else 0.0
    metrics = {"votes": len(votes), "verdicts": dict(counts),
               "majority": majority, "agreement": round(agreement, 3),
               "mean_score": round(mean, 3) if mean is not None else None,
               "score_variance": round(var, 4)}
    ok = agreement >= agree
    findings = [] if ok else [{"error": "hung", "agreement": round(agreement, 3),
                               "need": agree}]
    status = "pass" if ok else "fail"
    return mk_report("judge-vote", status, score=agreement, threshold=agree,
                     metrics=metrics, findings=findings,
                     extra={"verdict": majority})


# ---------------------------------------------------------------------------
# 6. dataset-build
# ---------------------------------------------------------------------------
_PYTYPE = {"string": str, "array": list, "object": dict, "null": type(None)}


def _type_ok(val, t):
    if t == "integer":
        return isinstance(val, int) and not isinstance(val, bool)
    if t == "number":
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    if t == "boolean":
        return isinstance(val, bool)
    py = _PYTYPE.get(t)
    return isinstance(val, py) if py else True


def _validate_json_schema(data, schema, path, errors):
    t = schema.get("type")
    if t and not _type_ok(data, t):
        errors.append({"path": path, "error": "type != " + t})
        return
    if isinstance(data, list):
        mi = schema.get("minItems", schema.get("min_items"))
        if mi is not None and len(data) < mi:
            errors.append({"path": path, "error": "minItems {} > {}".format(mi, len(data))})
        items = schema.get("items")
        if items:
            for i, it in enumerate(data):
                _validate_json_schema(it, items, "{}[{}]".format(path, i), errors)
    if isinstance(data, dict):
        for f in schema.get("required", []):
            if f not in data:
                errors.append({"path": path + "." + f, "error": "required missing"})
        for k, sub in schema.get("properties", {}).items():
            if k in data:
                _validate_json_schema(data[k], sub, path + "." + k, errors)


def validate_schema(data, schema):
    errors = []
    is_flat = "types" in schema or (
        "required" in schema and "properties" not in schema and "type" not in schema)
    if is_flat:
        recs = data if isinstance(data, list) else [data]
        req = schema.get("required", [])
        types = schema.get("types", {})
        for i, rec in enumerate(recs):
            if not isinstance(rec, dict):
                errors.append({"path": "[{}]".format(i), "error": "record not object"})
                continue
            for f in req:
                if f not in rec:
                    errors.append({"path": "[{}].{}".format(i, f), "error": "required missing"})
            for f, t in types.items():
                if f in rec and not _type_ok(rec[f], t):
                    errors.append({"path": "[{}].{}".format(i, f), "error": "type != " + t})
        mi = schema.get("min_items", schema.get("minItems"))
        if mi is not None and isinstance(data, list) and len(data) < mi:
            errors.append({"path": "$", "error": "min_items {} > {}".format(mi, len(data))})
        return errors
    _validate_json_schema(data, schema, "$", errors)
    return errors


def compute_dataset_build(dataset_path, schema_path):
    data, derr = _read_json(dataset_path)
    if derr == "missing":
        return observe("dataset-build", "dataset 無（{}）".format(dataset_path))
    schema, serr = _read_json(schema_path)
    if serr == "missing":
        return observe("dataset-build", "schema 無（{}）".format(schema_path))
    findings = []
    if derr:
        findings.append({"path": "$", "error": "dataset " + derr})
    if serr:
        findings.append({"path": "$", "error": "schema " + serr})
    if not findings:
        findings = validate_schema(data, schema)
    n = len(data) if isinstance(data, list) else 1
    metrics = {"records": n, "errors": len(findings)}
    status = "pass" if not findings else "fail"
    return mk_report("dataset-build", status, score=1.0 if not findings else 0.0,
                     metrics=metrics, findings=findings)


# ---------------------------------------------------------------------------
# 7. regression-guard
# ---------------------------------------------------------------------------
def compute_regression_guard(current_path, baseline_path, tol):
    cur, cerr = _read_json(current_path)
    if cerr:
        return observe("regression-guard", "current {}（{}）".format(cerr, current_path))
    base, berr = _read_json(baseline_path)
    if berr:
        return observe("regression-guard", "baseline {}（{}）".format(berr, baseline_path))
    cmap, bmap = _to_score_map(cur), _to_score_map(base)
    if not bmap:
        return observe("regression-guard", "baseline に数値指標無")
    regressions = []
    for k, bv in bmap.items():
        cv = cmap.get(k)
        if cv is None:
            regressions.append({"metric": k, "error": "missing in current",
                                "baseline": bv})
        elif cv < bv - tol:
            regressions.append({"metric": k, "baseline": bv, "current": cv,
                                "delta": round(cv - bv, 4)})
    metrics = {"metrics_checked": len(bmap), "regressed": len(regressions)}
    status = "pass" if not regressions else "fail"
    return mk_report("regression-guard", status,
                     score=1.0 if not regressions else 0.0,
                     metrics=metrics, findings=regressions)


# ---------------------------------------------------------------------------
# 8. halluc-rate
# ---------------------------------------------------------------------------
def compute_halluc_rate(path, max_rate):
    text = _read_text(path)
    if text is None:
        return observe("halluc-rate", "file 無（{}）".format(path))
    lines = text.splitlines()
    claim_idx = [i for i, l in enumerate(lines) if is_claim(l)]
    if not claim_idx:
        return observe("halluc-rate", "主張 0 件")
    unsupported = []
    for i in claim_idx:
        window = lines[max(0, i - 1):i + 2]
        if not any(line_has_url(w) for w in window):
            unsupported.append(lines[i].strip()[:60])
    rate = len(unsupported) / len(claim_idx)
    metrics = {"claims": len(claim_idx), "unsupported": len(unsupported)}
    ok = max_rate is None or rate <= max_rate
    findings = [{"unsupported_claim": u} for u in unsupported] if not ok else []
    return gate("halluc-rate", rate, max_rate, ok, "max",
                score=rate, metrics=metrics, findings=findings)


# ---------------------------------------------------------------------------
# 9. success-rate
# ---------------------------------------------------------------------------
def _scan_outcomes(dir_path):
    succ = cap = 0
    p = Path(dir_path)
    if not p.is_dir():
        return None
    for f in sorted(p.rglob("*")):
        if not f.is_file():
            continue
        txt = _read_text(f)
        if txt is None:
            continue
        for tok in SUCCESS_TOKENS:
            succ += txt.count(tok)
        for tok in CAP_TOKENS:
            cap += txt.count(tok)
        if f.suffix == ".json":
            try:
                data = json.loads(txt)
            except ValueError:
                data = None
            recs = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
            for rec in recs:
                if isinstance(rec, dict) and "outcome" in rec:
                    o = str(rec["outcome"]).lower()
                    if o in JSON_SUCCESS:
                        succ += 1
                    elif o in JSON_CAP:
                        cap += 1
    return succ, cap


def compute_success_rate(dir_path, min_rate):
    scanned = _scan_outcomes(dir_path)
    if scanned is None:
        return observe("success-rate", "dir 無（{}）".format(dir_path))
    succ, cap = scanned
    total = succ + cap
    if total == 0:
        return observe("success-rate", "goal-loop 帰結 0 件")
    rate = succ / total
    metrics = {"success": succ, "cap": cap, "total": total}
    ok = min_rate is None or rate >= min_rate
    return gate("success-rate", rate, min_rate, ok, "min",
                score=rate, metrics=metrics)


# ---------------------------------------------------------------------------
# 10. ab-eval
# ---------------------------------------------------------------------------
def compute_ab_eval(a_path, b_path, margin):
    da, aerr = _read_json(a_path)
    if aerr:
        return observe("ab-eval", "A {}（{}）".format(aerr, a_path))
    db, berr = _read_json(b_path)
    if berr:
        return observe("ab-eval", "B {}（{}）".format(berr, b_path))
    a, b = _to_score_list(da), _to_score_list(db)
    if not a or not b:
        return observe("ab-eval", "A/B に数値スコア無")
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    diff = abs(ma - mb)
    winner = "a" if ma > mb else ("b" if mb > ma else "tie")
    decisive = winner != "tie" and diff >= margin
    metrics = {"mean_a": round(ma, 4), "mean_b": round(mb, 4),
               "diff": round(diff, 4), "winner": winner if decisive else "tie",
               "n_a": len(a), "n_b": len(b)}
    status = "pass" if decisive else "fail"
    findings = [] if decisive else [{"error": "within margin (tie)",
                                     "diff": round(diff, 4), "margin": margin}]
    return mk_report("ab-eval", status, score=diff, threshold=margin,
                     metrics=metrics, findings=findings,
                     extra={"winner": winner if decisive else "tie"})


# ---------------------------------------------------------------------------
# 11. review-repro
# ---------------------------------------------------------------------------
def compute_review_repro(sets_path, min_jaccard):
    data, err = _read_json(sets_path)
    if err:
        return observe("review-repro", "sets {}（{}）".format(err, sets_path))
    if not isinstance(data, list):
        return observe("review-repro", "sets は list でない")
    sets = [set(str(x) for x in s) for s in data if isinstance(s, list)]
    if len(sets) < 2:
        return observe("review-repro", "finding set が 2 未満（再現性測定不能）")
    pair_j = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            pair_j.append(jaccard(sets[i], sets[j]))
    mean_j = sum(pair_j) / len(pair_j)
    metrics = {"sets": len(sets), "pairs": len(pair_j),
               "min_pair": round(min(pair_j), 3), "max_pair": round(max(pair_j), 3)}
    ok = min_jaccard is None or mean_j >= min_jaccard
    return gate("review-repro", mean_j, min_jaccard, ok, "min",
                score=mean_j, metrics=metrics)


# ---------------------------------------------------------------------------
# self-test（hermetic fixture）
# ---------------------------------------------------------------------------
def _mktemp():
    return tempfile.mkdtemp(prefix="eval-st-")


def _w(path, text):
    Path(path).write_text(text, encoding="utf-8")
    return str(path)


def _wj(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(path)


def st_citation_coverage():
    d = _mktemp()
    try:
        good = _w(os.path.join(d, "g.md"),
                  "## 市場規模\n"
                  "- 国内AI市場は前年比40%増と報告されている [出典](https://ex.com/a)\n"
                  "- 導入企業は1,200社に達した [調査](https://ex.com/b)\n")
        bad = _w(os.path.join(d, "b.md"),
                 "## 主張\n- 国内AI市場は前年比40%増だ\n- 導入企業は1,200社に達した\n")
        a = compute_citation_coverage(good, 0.8)["status"] == "pass"
        b = compute_citation_coverage(bad, 0.8)["status"] == "fail"
        c = compute_citation_coverage(os.path.join(d, "none.md"), 0.8)["status"] == "observe"
        return ("citation-coverage", a and b and c, "good={} bad={} obs={}".format(a, b, c))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_style_score():
    d = _mktemp()
    try:
        good = _w(os.path.join(d, "g.md"),
                  "先週はメールリストが少しだけ伸びた。"
                  "とはいえ、配信の承認がボトルネックになっているのは変わらない、"
                  "という現実にまた向き合うことになる。"
                  "小さく試す。それを続ける。\n")
        bad = _w(os.path.join(d, "b.md"),
                 "これは絶対に必ず成功します。当然です。いかがでしたか。結論として最高です。\n")
        a = compute_style_score(good, 0.7)["status"] == "pass"
        b = compute_style_score(bad, 0.7)["status"] == "fail"
        c = compute_style_score(os.path.join(d, "none.md"), 0.7)["status"] == "observe"
        return ("style-score", a and b and c, "good={} bad={} obs={}".format(a, b, c))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_standup_novelty():
    d = _mktemp()
    try:
        prev = _w(os.path.join(d, "p.md"),
                  "## 変化\n- 製品 A を OSS 公開した\n- 決定 X を採択した\n")
        good = _w(os.path.join(d, "g.md"),
                  "## 変化\n- 製品 B のセキュリティ修正を3件マージした\n"
                  "- 製品 C の CI を有効化した\n")
        bad = _w(os.path.join(d, "b.md"),
                 "## 変化\n- 製品 A を OSS 公開した\n- 決定 X を採択した\n")
        a = compute_standup_novelty(good, prev, 0.5)["status"] == "pass"
        b = compute_standup_novelty(bad, prev, 0.5)["status"] == "fail"
        c = compute_standup_novelty(good, os.path.join(d, "none.md"), 0.5)["status"] == "observe"
        return ("standup-novelty", a and b and c, "good={} bad={} obs={}".format(a, b, c))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_commit_score():
    good = ["feat(eval): 決定論ゲートで自己申告を排除する理由を明記した",
            "fix(hook): secret 誤検知を修正し本物鍵は取りこぼさないようにした"]
    bad = ["update", "更新", "wip"]
    a = compute_commit_score(good, 0.7)["status"] == "pass"
    b = compute_commit_score(bad, 0.7)["status"] == "fail"
    c = compute_commit_score(None, 0.7)["status"] == "observe"
    # git 経路も検証（利用可能なら temp repo で実測）
    g = True
    if shutil.which("git"):
        d = _mktemp()
        try:
            subprocess.run(["git", "-C", d, "init", "-q"], check=False)
            subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=False)
            subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=False)
            _w(os.path.join(d, "f"), "x")
            subprocess.run(["git", "-C", d, "add", "f"], check=False)
            subprocess.run(["git", "-C", d, "commit", "-q", "-m",
                            "feat(x): 理由を明記して空虚語を避けた説明的コミット"], check=False)
            # ⚠️ `--since 1970-01-01` は git 2.53 で **黙って 0 件**を返す
            #    （エポックそのものを無効として扱う。1980-01-01 以降は正常）。
            #    配布物の self-test がこれを使っていたため、受け取った人が最初に
            #    verify を叩くと落ちる。`@0` は明示的な epoch 指定で解釈が揺れない。
            msgs = _git_messages("@0", d)
            g = msgs is not None and len(msgs) == 1
        finally:
            shutil.rmtree(d, ignore_errors=True)
    return ("commit-score", a and b and c and g,
            "good={} bad={} obs={} git={}".format(a, b, c, g))


def st_judge_vote():
    d = _mktemp()
    try:
        good = _wj(os.path.join(d, "g.json"),
                   [{"verdict": "pass", "score": 0.9},
                    {"verdict": "pass", "score": 0.85},
                    {"verdict": "pass", "score": 0.92}])
        bad = _wj(os.path.join(d, "b.json"),
                  [{"verdict": "pass", "score": 0.6},
                   {"verdict": "fail", "score": 0.4},
                   {"verdict": "hold", "score": 0.5}])
        a = compute_judge_vote(good, 0.66)["status"] == "pass"
        b = compute_judge_vote(bad, 0.66)["status"] == "fail"
        c = compute_judge_vote(os.path.join(d, "none.json"), 0.66)["status"] == "observe"
        return ("judge-vote", a and b and c, "good={} bad={} obs={}".format(a, b, c))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_dataset_build():
    d = _mktemp()
    try:
        schema = _wj(os.path.join(d, "s.json"),
                     {"required": ["prompt", "expected"],
                      "types": {"prompt": "string", "expected": "string"},
                      "min_items": 2})
        good = _wj(os.path.join(d, "g.json"),
                   [{"prompt": "q1", "expected": "a1"},
                    {"prompt": "q2", "expected": "a2"}])
        bad = _wj(os.path.join(d, "b.json"), [{"prompt": "q1"}])
        a = compute_dataset_build(good, schema)["status"] == "pass"
        b = compute_dataset_build(bad, schema)["status"] == "fail"
        c = compute_dataset_build(os.path.join(d, "none.json"), schema)["status"] == "observe"
        return ("dataset-build", a and b and c, "good={} bad={} obs={}".format(a, b, c))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_regression_guard():
    d = _mktemp()
    try:
        base = _wj(os.path.join(d, "base.json"),
                   {"coverage": 0.9, "style": 0.8})
        good = _wj(os.path.join(d, "g.json"), {"coverage": 0.92, "style": 0.8})
        bad = _wj(os.path.join(d, "b.json"), {"coverage": 0.7, "style": 0.8})
        a = compute_regression_guard(good, base, 0.0)["status"] == "pass"
        b = compute_regression_guard(bad, base, 0.0)["status"] == "fail"
        c = compute_regression_guard(good, os.path.join(d, "none.json"), 0.0)["status"] == "observe"
        return ("regression-guard", a and b and c, "good={} bad={} obs={}".format(a, b, c))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_halluc_rate():
    d = _mktemp()
    try:
        good = _w(os.path.join(d, "g.md"),
                  "- 市場は40%増だと報告された https://ex.com/a\n"
                  "- 企業数は1,200社に達した https://ex.com/b\n")
        bad = _w(os.path.join(d, "b.md"),
                 "- 市場は40%増だと言われている\n- 企業数は1,200社に達した\n")
        a = compute_halluc_rate(good, 0.2)["status"] == "pass"
        b = compute_halluc_rate(bad, 0.2)["status"] == "fail"
        c = compute_halluc_rate(os.path.join(d, "none.md"), 0.2)["status"] == "observe"
        return ("halluc-rate", a and b and c, "good={} bad={} obs={}".format(a, b, c))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_success_rate():
    d = _mktemp()
    try:
        gd = os.path.join(d, "good")
        os.makedirs(gd)
        _w(os.path.join(gd, "1.log"), "✅ GOAL MET: coverage\n")
        _w(os.path.join(gd, "2.log"), "✅ GOAL MET: tests\n")
        _w(os.path.join(gd, "3.log"), "🛑 CAP REACHED: flaky\n")
        bd = os.path.join(d, "bad")
        os.makedirs(bd)
        _w(os.path.join(bd, "1.log"), "🛑 CAP REACHED: a\n")
        _w(os.path.join(bd, "2.log"), "🛑 CAP REACHED: b\n")
        _w(os.path.join(bd, "3.log"), "✅ GOAL MET: c\n")
        empty = os.path.join(d, "empty")
        os.makedirs(empty)
        a = compute_success_rate(gd, 0.6)["status"] == "pass"
        b = compute_success_rate(bd, 0.6)["status"] == "fail"
        c = compute_success_rate(empty, 0.6)["status"] == "observe"
        c2 = compute_success_rate(os.path.join(d, "nodir"), 0.6)["status"] == "observe"
        return ("success-rate", a and b and c and c2,
                "good={} bad={} obs={}/{}".format(a, b, c, c2))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_ab_eval():
    d = _mktemp()
    try:
        a_json = _wj(os.path.join(d, "a.json"), [0.5, 0.52, 0.48])
        b_hi = _wj(os.path.join(d, "bhi.json"), [0.9, 0.88, 0.92])
        b_eq = _wj(os.path.join(d, "beq.json"), [0.5, 0.52, 0.48])
        a = compute_ab_eval(a_json, b_hi, 0.1)
        good = a["status"] == "pass" and a["winner"] == "b"
        bad = compute_ab_eval(a_json, b_eq, 0.1)["status"] == "fail"
        obs = compute_ab_eval(a_json, os.path.join(d, "none.json"), 0.1)["status"] == "observe"
        return ("ab-eval", good and bad and obs,
                "good={} bad={} obs={}".format(good, bad, obs))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def st_review_repro():
    d = _mktemp()
    try:
        good = _wj(os.path.join(d, "g.json"),
                   [["a", "b", "c"], ["a", "b", "c"], ["a", "b"]])
        bad = _wj(os.path.join(d, "b.json"),
                  [["a", "b"], ["x", "y"], ["p", "q"]])
        single = _wj(os.path.join(d, "s.json"), [["a", "b"]])
        a = compute_review_repro(good, 0.7)["status"] == "pass"
        b = compute_review_repro(bad, 0.7)["status"] == "fail"
        c = compute_review_repro(single, 0.7)["status"] == "observe"
        return ("review-repro", a and b and c, "good={} bad={} obs={}".format(a, b, c))
    finally:
        shutil.rmtree(d, ignore_errors=True)


NAME_TO_ST = {
    "citation-coverage": st_citation_coverage,
    "style-score": st_style_score,
    "standup-novelty": st_standup_novelty,
    "commit-score": st_commit_score,
    "judge-vote": st_judge_vote,
    "dataset-build": st_dataset_build,
    "regression-guard": st_regression_guard,
    "halluc-rate": st_halluc_rate,
    "success-rate": st_success_rate,
    "ab-eval": st_ab_eval,
    "review-repro": st_review_repro,
}
ALL_ST = [NAME_TO_ST[k] for k in [
    "citation-coverage", "style-score", "standup-novelty", "commit-score",
    "judge-vote", "dataset-build", "regression-guard", "halluc-rate",
    "success-rate", "ab-eval", "review-repro",
]]


def run_selftests(fns):
    results = []
    for fn in fns:
        try:
            name, passed, detail = fn()
        except Exception as e:  # noqa: BLE001 — self-test は harness を落とさない
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
    root = _default_root()
    sub = args.sub
    if sub == "citation-coverage":
        return compute_citation_coverage(args.file, args.min)
    if sub == "style-score":
        return compute_style_score(args.file, args.min)
    if sub == "standup-novelty":
        return compute_standup_novelty(args.cur, args.prev, args.max_dup)
    if sub == "commit-score":
        msgs = _git_messages(args.since, args.root or root)
        return compute_commit_score(msgs, args.min)
    if sub == "judge-vote":
        return compute_judge_vote(args.votes, args.agree)
    if sub == "dataset-build":
        return compute_dataset_build(args.dataset, args.schema)
    if sub == "regression-guard":
        return compute_regression_guard(args.current, args.baseline, args.tolerance)
    if sub == "halluc-rate":
        return compute_halluc_rate(args.file, args.max_rate)
    if sub == "success-rate":
        return compute_success_rate(args.dir, args.min)
    if sub == "ab-eval":
        return compute_ab_eval(args.a, args.b, args.margin)
    if sub == "review-repro":
        return compute_review_repro(args.sets, args.min_jaccard)
    raise ValueError("unknown subcommand: {}".format(sub))


def build_parser():
    p = argparse.ArgumentParser(
        prog="eval-harness.py",
        description="crew 評価（B系）決定論採点 共通ツール")
    p.add_argument("--self-test", action="store_true", help="全11サブの hermetic 自己テスト")
    sub = p.add_subparsers(dest="sub")

    def common(sp):
        sp.add_argument("--json", action="store_true", help="JSON 出力")
        sp.add_argument("--self-test", action="store_true", help="このサブのみ自己テスト")

    sp = sub.add_parser("citation-coverage", help="記事の主張に対する出典充足率")
    sp.add_argument("--file")
    sp.add_argument("--min", type=float, default=None)
    common(sp)

    sp = sub.add_parser("style-score", help="公開文の文体適合（AI句/文長分散/断定過多）")
    sp.add_argument("--file")
    sp.add_argument("--min", type=float, default=None)
    common(sp)

    sp = sub.add_parser("standup-novelty", help="standup の前版重複率")
    sp.add_argument("--cur")
    sp.add_argument("--prev")
    sp.add_argument("--max-dup", type=float, default=0.5)
    common(sp)

    sp = sub.add_parser("commit-score", help="git 範囲の commit msg 品質")
    sp.add_argument("--since", default="7 days ago")
    sp.add_argument("--min", type=float, default=None)
    sp.add_argument("--root", default=None)
    common(sp)

    sp = sub.add_parser("judge-vote", help="複数 vote の多数決+票分散合議")
    sp.add_argument("--votes")
    sp.add_argument("--agree", type=float, default=0.66)
    common(sp)

    sp = sub.add_parser("dataset-build", help="golden dataset の schema 検証")
    sp.add_argument("--dataset")
    sp.add_argument("--schema")
    common(sp)

    sp = sub.add_parser("regression-guard", help="現行 vs baseline スコアの回帰検知")
    sp.add_argument("--current")
    sp.add_argument("--baseline")
    sp.add_argument("--tolerance", type=float, default=0.0)
    common(sp)

    sp = sub.add_parser("halluc-rate", help="doc の unsupported 主張率")
    sp.add_argument("--file")
    sp.add_argument("--max-rate", type=float, default=None)
    common(sp)

    sp = sub.add_parser("success-rate", help="goal-loop 帰結群の成功率")
    sp.add_argument("--dir")
    sp.add_argument("--min", type=float, default=None)
    common(sp)

    sp = sub.add_parser("ab-eval", help="2 スコアセットの勝者+マージン判定")
    sp.add_argument("--a")
    sp.add_argument("--b")
    sp.add_argument("--margin", type=float, default=0.05)
    common(sp)

    sp = sub.add_parser("review-repro", help="複数 finding セットの Jaccard 再現性")
    sp.add_argument("--sets")
    sp.add_argument("--min-jaccard", type=float, default=0.5)
    common(sp)

    return p


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
    report = _dispatch(args)
    emit(report, getattr(args, "json", False))
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
