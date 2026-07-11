#!/usr/bin/env python3
# =============================================================
# refute-vote.py -- deterministic aggregation gate for adversarial refute voting.
#
# Why: do not adopt a claim just because it "looks right". Put it to N independent
#   refute votes; if a strict majority vote "refuted", stop adoption. No self-report:
#   this gate never calls an LLM and never asks the model to judge -- it mechanically
#   tallies the refuted:bool verdicts each judge produced.
#
# Decision (majority = strictly more than half of the valid votes):
#   refuted_count * 2 >  valid_count  -> STOP    (do not adopt / exit 1)
#   refuted_count * 2 <= valid_count  -> ADOPT   (may adopt    / exit 0)
#   valid_count == 0 / bad input      -> ABSTAIN (undecidable  / exit 2)
#     (empty output != zero: a state where no votes were obtained is NOT collapsed
#      into "no refutation = adopt".)
#
# Input: a JSON array of dicts each carrying refuted:bool.
#   [{"refuted": true, "reason": "..."}, {"refuted": false}, ...]
#   Elements whose "refuted" is not a bool are counted as invalid and excluded.
#
# Usage:
#   refute-vote.py --votes <votes.json> [--json]
#   cat votes.json | refute-vote.py --votes - [--json]   # stdin
#   refute-vote.py --self-test                            # deterministic gate (--check)
#
# Dependencies: python3 standard library only (runs as-is on BSD/macOS; no gh/aws).
# =============================================================
import argparse
import json
import os
import subprocess
import sys
import tempfile

# decision -> exit code mapping (kept in one place)
EXIT = {"adopt": 0, "stop": 1, "abstain": 2}


def aggregate(raw):
    """Take parsed JSON (expected: list) and return the tally as a dict.

    Returned dict:
      decision : "adopt" | "stop" | "abstain"
      total    : number of array elements
      valid    : number of valid votes (elements whose refuted is a bool)
      invalid  : number of invalid votes (refuted missing / wrong type)
      refuted  : number of votes that refuted the claim
      upheld   : number of votes that did not refute
      majority : majority threshold (stop when refuted exceeds this count)
      reason   : human-readable explanation of the decision
    """
    if not isinstance(raw, list):
        return {
            "decision": "abstain",
            "total": 0, "valid": 0, "invalid": 0,
            "refuted": 0, "upheld": 0, "majority": 0,
            "reason": "input is not a JSON array",
        }

    total = len(raw)
    valid_flags = []
    invalid = 0
    for v in raw:
        # bool is a subclass of int, but isinstance(1, bool) is False.
        # refuted must be strictly true/false to count as a valid vote.
        if isinstance(v, dict) and isinstance(v.get("refuted"), bool):
            valid_flags.append(v["refuted"])
        else:
            invalid += 1

    valid = len(valid_flags)
    refuted = sum(1 for x in valid_flags if x)
    upheld = valid - refuted
    # strict majority: stop adoption when refutations exceed half the valid votes
    majority_threshold = valid // 2  # "exceeding" this = floor(valid/2)+1 or more

    if valid == 0:
        decision = "abstain"
        reason = "no valid refute votes (refuted:bool absent in all elements)"
    elif refuted * 2 > valid:
        decision = "stop"
        reason = "majority refuted: %d/%d refute votes are true" % (refuted, valid)
    else:
        decision = "adopt"
        reason = "refute not majority: %d/%d refute votes are true" % (refuted, valid)

    return {
        "decision": decision,
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "refuted": refuted,
        "upheld": upheld,
        "majority": majority_threshold,
        "reason": reason,
    }


def load_json(path):
    """Read JSON from stdin when path is '-', otherwise from the given file."""
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def format_report(m):
    lines = [
        "refute-vote: %s" % m["decision"].upper(),
        "  votes total=%d valid=%d invalid=%d" % (m["total"], m["valid"], m["invalid"]),
        "  refuted=%d upheld=%d (majority needs refuted > %d)"
        % (m["refuted"], m["upheld"], m["majority"]),
        "  reason: %s" % m["reason"],
    ]
    return "\n".join(lines)


# --- self-test fixtures (hermetic: write JSON to a tempfile, verify via load path) ---
# each case = (fixture list, expected decision)
SELF_TEST_CASES = [
    # majority refuted -> stop (2 of 3 refute)
    ([{"refuted": True, "reason": "factual error"},
      {"refuted": True, "reason": "source does not support the claim"},
      {"refuted": False}], "stop"),
    # minority refuted -> may adopt (1 of 3 refute)
    ([{"refuted": False},
      {"refuted": False},
      {"refuted": True, "reason": "minor wording"}], "adopt"),
    # unanimous refute -> stop
    ([{"refuted": True}, {"refuted": True}, {"refuted": True}], "stop"),
    # unanimous no-refute -> may adopt
    ([{"refuted": False}, {"refuted": False}, {"refuted": False}], "adopt"),
    # even split (2 refute of 4) is NOT a majority -> may adopt
    ([{"refuted": True}, {"refuted": True},
      {"refuted": False}, {"refuted": False}], "adopt"),
    # zero valid votes (refuted missing) -> undecidable (never collapse to adopt)
    ([{"reason": "no verdict"}, {"comment": "n/a"}], "abstain"),
    # empty array -> undecidable
    ([], "abstain"),
    # wrong-type entries are excluded as invalid (3 valid, 2 refute -> stop)
    ([{"refuted": "yes"}, {"refuted": 1},
      {"refuted": True}, {"refuted": True}, {"refuted": False}], "stop"),
]


def run_self_test():
    passed = 0
    failed = 0
    tmp_paths = []
    try:
        for idx, (fixture, expected) in enumerate(SELF_TEST_CASES):
            # exercise the same load->aggregate path used in production (via tempfile)
            fd, path = tempfile.mkstemp(suffix="_refute_%d.json" % idx)
            tmp_paths.append(path)
            os.write(fd, json.dumps(fixture).encode("utf-8"))
            os.close(fd)

            m = aggregate(load_json(path))
            got = m["decision"]
            ok = (got == expected)
            passed += ok
            failed += (not ok)
            print("[self-test] case %d: expect=%-7s got=%-7s -> %s"
                  % (idx, expected, got, "OK" if ok else "FAIL"))
            if not ok:
                sys.stderr.write("  detail: %s\n" % m["reason"])

        # Verify the exit-code contract by observing the REAL process exit code.
        # This is the exact regression --self-test must catch: a scrambled /
        # fail-open EXIT map (e.g. abstain->0) would make an unreadable or
        # zero-vote input look like "adopt" to a `&&` caller. We drive one
        # fixture per decision through this very script as a subprocess and
        # check os.WEXITSTATUS, so the check cannot be fooled by editing a
        # duplicated literal — only the true main()->sys.exit(EXIT[...]) path is
        # trusted. Expected: adopt->0 (success), stop->1, abstain->2.
        exit_probes = [
            ([{"refuted": False}, {"refuted": False}], 0),  # adopt
            ([{"refuted": True}, {"refuted": True}], 1),    # stop
            ([{"reason": "no verdict"}], 2),                # abstain (no valid vote)
        ]
        for probe_idx, (fixture, want_code) in enumerate(exit_probes):
            fd, path = tempfile.mkstemp(suffix="_refute_exit_%d.json" % probe_idx)
            tmp_paths.append(path)
            os.write(fd, json.dumps(fixture).encode("utf-8"))
            os.close(fd)
            rc = subprocess.call(
                [sys.executable, os.path.abspath(__file__), "--votes", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ok = (rc == want_code)
            passed += ok
            failed += (not ok)
            print("[self-test] exit-probe %d: want=%d got=%d -> %s"
                  % (probe_idx, want_code, rc, "OK" if ok else "FAIL"))

        if failed == 0:
            print("[self-test] RESULT: PASS (%d/%d cases)" % (passed, passed + failed))
            return 0
        print("[self-test] RESULT: FAIL (%d/%d cases)" % (passed, passed + failed))
        return 1
    finally:
        for p in tmp_paths:
            if p and os.path.exists(p):
                os.remove(p)


def main():
    ap = argparse.ArgumentParser(
        description="Deterministically aggregate adversarial refute votes; "
                    "stop adoption on a strict majority refutation."
    )
    ap.add_argument("--votes",
                    help="JSON array file of dicts carrying refuted:bool ('-' for stdin)")
    ap.add_argument("--json", action="store_true",
                    help="print the tally as machine-readable JSON")
    ap.add_argument("--self-test", action="store_true",
                    help="verify majority-refuted->stop / minority->adopt on internal fixtures (--check)")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(run_self_test())

    if not args.votes:
        ap.error("one of --votes or --self-test is required")

    try:
        raw = load_json(args.votes)
    except (OSError, ValueError) as e:
        # read/parse failure is "unknown" = abstain (never misread as zero votes)
        m = aggregate(None)
        m["reason"] = "load error: %s" % e
        if args.json:
            print(json.dumps(m, ensure_ascii=False))
        else:
            sys.stderr.write(format_report(m) + "\n")
        sys.exit(EXIT["abstain"])

    m = aggregate(raw)
    if args.json:
        print(json.dumps(m, ensure_ascii=False))
    else:
        out = format_report(m)
        if m["decision"] == "adopt":
            print(out)
        else:
            sys.stderr.write(out + "\n")

    sys.exit(EXIT[m["decision"]])


if __name__ == "__main__":
    main()
