#!/usr/bin/env python3
# =============================================================
# judge-vote.py — judge-rubric-vote
#
# Goal (deterministic judge panel): never trust a single judge's self-report.
# Given N independent judge votes, each {"verdict": <any>, "score": <number?>},
# aggregate them into a majority verdict + agreement rate + score spread, and let
# the verdict stand ONLY when agreement clears a threshold. A split panel is a
# "hung" jury and FAILS — it is never spun into a pass.
#
#   agreement = (votes for the majority verdict) / (valid votes)
#   pass  iff  agreement >= --agree (default 0.66)
#   otherwise the panel is "hung" -> fail
#
# Aggregation is deterministic (no LLM call): a Counter over str(verdict) picks
# the majority; scores contribute mean_score and score_variance (population
# variance) purely as visibility — "verdicts agree but the scores disagree".
#
# Non-goals / safety:
#   - DETECTION ONLY. It reads votes and prints a verdict; it never edits files,
#     never merges, never deploys, never re-runs the judges.
#   - python3 stdlib only. No network, no gh/aws, no third-party packages.
#
# Usage:
#   python3 scripts/judge-vote.py --votes <votes.json|-> [--agree 0.66] [--json]
#   python3 scripts/judge-vote.py --self-test        # deterministic health check (hermetic)
#
#   --votes -   reads the JSON vote list from stdin.
#
# Exit codes (DELIBERATE — a standalone gate must fail closed):
#   0 = pass    (agreement >= threshold; majority verdict adopted)
#   1 = fail    (hung: agreement < threshold; the panel is split)
#   2 = observe (no/invalid votes: missing file, non-JSON, not-a-list, 0 valid votes)
#
#   observe is 2, NOT 0, on purpose. If a missing votes file exited 0 a `&&`
#   caller would read "no votes" as consensus (fail-open). 2 = "unknown, do not
#   adopt" — the kit's abstain/unknown convention — so absence can never be
#   mistaken for agreement.
# =============================================================
import argparse
import json
import math
import os
import shutil
import statistics
import sys
import tempfile
from collections import Counter
from pathlib import Path

SUB = "judge-vote"
OBSERVE_NOTE = "observe: not measurable (insufficient / invalid input)"


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------
def _read_text(path):
    """Return the file's text, or None when it is missing / unreadable."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, OSError):
        return None


def _read_json(path):
    """(data, error). '-' reads stdin. missing -> (None,'missing');
    unparseable -> (None,'invalid: ...')."""
    if path == "-":
        txt = sys.stdin.read()
    else:
        txt = _read_text(path)
        if txt is None:
            return None, "missing"
    try:
        return json.loads(txt), None
    except (ValueError, TypeError) as e:
        return None, "invalid: {}".format(e)


def _num(v):
    """Coerce a vote's score to float, or None. Bools are not numbers; a nested
    {"score": ...} dict is unwrapped."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        # Reject non-finite scores (NaN / Infinity): they are malformed input and
        # would otherwise emit non-RFC-8259 JSON tokens in --json (jq / strict
        # JSON.parse reject them). Treat like a missing score.
        return float(v) if math.isfinite(v) else None
    if isinstance(v, dict) and "score" in v:
        return _num(v["score"])
    return None


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def mk_report(status, score=None, threshold=None,
              metrics=None, findings=None, notes=None, extra=None):
    r = {"subcommand": SUB, "status": status}
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


def observe(note):
    return mk_report("observe", notes=[note])


def exit_code(report):
    status = report.get("status")
    if status == "fail":
        return 1
    if status == "observe":
        return 2
    return 0


def emit(r, as_json):
    if as_json:
        print(json.dumps(r, ensure_ascii=False))
        return
    print("# judge-vote — {}".format(r["status"]))
    if "score" in r:
        print("  score: {}".format(r["score"]))
    if r.get("threshold") is not None:
        print("  threshold: {}".format(r["threshold"]))
    if "verdict" in r:
        print("  verdict: {}".format(r["verdict"]))
    for k, v in (r.get("metrics") or {}).items():
        print("  {}: {}".format(k, v))
    for n in r.get("notes", []):
        print("  note: {}".format(n))
    if r["status"] == "observe":
        print("  {}".format(OBSERVE_NOTE))
    findings = r.get("findings", [])
    if findings:
        print("  findings: {}".format(len(findings)))
        for f in findings:
            print("  - " + (f if isinstance(f, str)
                            else json.dumps(f, ensure_ascii=False)))


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------
def compute_judge_vote(votes_path, agree):
    data, err = _read_json(votes_path)
    if err:
        return observe("votes {} ({})".format(err, votes_path))
    if not isinstance(data, list):
        return observe("votes is not a list")
    votes = [v for v in data if isinstance(v, dict) and "verdict" in v]
    if not votes:
        return observe("no valid votes")
    counts = Counter(str(v["verdict"]) for v in votes)
    majority, mc = counts.most_common(1)[0]
    agreement = mc / len(votes)
    scores = [n for v in votes if (n := _num(v.get("score"))) is not None]
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
    return mk_report(status, score=agreement, threshold=agree,
                     metrics=metrics, findings=findings,
                     extra={"verdict": majority})


# ---------------------------------------------------------------------------
# self-test (hermetic tempfile fixtures, real load path, no hardcoded pass)
# ---------------------------------------------------------------------------
def _wj(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _selftest():
    d = tempfile.mkdtemp(prefix="judge-vote-st-")
    checks = []
    try:
        # 3/3 unanimous -> pass (exit 0)
        unanimous = _wj(os.path.join(d, "unanimous.json"),
                        [{"verdict": "pass", "score": 0.9},
                         {"verdict": "pass", "score": 0.85},
                         {"verdict": "pass", "score": 0.92}])
        r = compute_judge_vote(unanimous, 0.66)
        checks.append(("unanimous->pass",
                       r["status"] == "pass" and exit_code(r) == 0))

        # 3-way split (agreement 1/3 = 0.33) -> fail (exit 1)
        split = _wj(os.path.join(d, "split.json"),
                    [{"verdict": "pass", "score": 0.6},
                     {"verdict": "fail", "score": 0.4},
                     {"verdict": "hold", "score": 0.5}])
        r = compute_judge_vote(split, 0.66)
        checks.append(("split->fail",
                       r["status"] == "fail" and exit_code(r) == 1
                       and r["findings"][0]["error"] == "hung"))

        # missing file -> observe (exit 2)
        r = compute_judge_vote(os.path.join(d, "does-not-exist.json"), 0.66)
        checks.append(("missing->observe",
                       r["status"] == "observe" and exit_code(r) == 2))

        # non-list JSON -> observe
        nonlist = _wj(os.path.join(d, "nonlist.json"), {"verdict": "pass"})
        r = compute_judge_vote(nonlist, 0.66)
        checks.append(("nonlist->observe", r["status"] == "observe"))

        # votes lacking "verdict" are ignored: 2 valid pass + 1 invalid -> pass, votes=2
        mixed = _wj(os.path.join(d, "mixed.json"),
                    [{"verdict": "pass", "score": 0.8},
                     {"score": 0.99},                       # no verdict -> ignored
                     {"verdict": "pass", "score": 0.7}])
        r = compute_judge_vote(mixed, 0.66)
        checks.append(("invalid-ignored->pass,votes=2",
                       r["status"] == "pass" and r["metrics"]["votes"] == 2))

        # threshold is LIVE: 2-of-3 (agreement 0.667) straddles the bar.
        straddle = _wj(os.path.join(d, "straddle.json"),
                       [{"verdict": "pass", "score": 0.8},
                        {"verdict": "pass", "score": 0.7},
                        {"verdict": "fail", "score": 0.3}])
        r_lo = compute_judge_vote(straddle, 0.66)   # 0.667 >= 0.66 -> pass
        r_hi = compute_judge_vote(straddle, 0.7)    # 0.667 <  0.70 -> fail
        checks.append(("straddle @0.66->pass",
                       r_lo["status"] == "pass" and exit_code(r_lo) == 0))
        checks.append(("straddle @0.70->fail",
                       r_hi["status"] == "fail" and exit_code(r_hi) == 1))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    ok = all(passed for _, passed in checks)
    for name, passed in checks:
        print("  {} {}".format("PASS" if passed else "FAIL", name))
    print("self-test: {}/{} passed".format(
        sum(1 for _, p in checks if p), len(checks)))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="judge-vote.py",
        description="Aggregate N judge votes into a majority verdict gated by "
                    "an agreement threshold (split = hung = fail).")
    p.add_argument("--votes", help="JSON list file of {verdict, score?}; '-' = stdin")
    p.add_argument("--agree", type=float, default=0.66,
                   help="agreement threshold to pass (default 0.66)")
    p.add_argument("--json", action="store_true", help="emit the report as JSON")
    p.add_argument("--self-test", action="store_true",
                   help="run the hermetic deterministic health check (exit 0 = PASS)")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    if args.self_test:
        return _selftest()
    if not args.votes:
        build_parser().print_help()
        return 2
    report = compute_judge_vote(args.votes, args.agree)
    emit(report, args.json)
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
