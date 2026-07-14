#!/usr/bin/env bash
# =============================================================
# commit-convention-check.sh — a deterministic commit-message gate (read-only).
#
# Purpose: check ONE commit message (--msg / a positional arg / --file / --stdin)
#          and reject it with exit 1 when it violates the convention. Three axes,
#          ALL of which must hold to pass:
#            1) prefix    : a Conventional Commits type prefix
#                           (feat/fix/docs/chore/refactor/test/perf/build/ci/
#                           style/revert — extendable via CCC_EXTRA_PREFIX).
#            2) not_empty : the description is >= 6 chars and is not a bare
#                           placeholder ("update", "wip", "fix", … alone).
#            3) has_why   : the description is >= 25 chars, OR it contains a
#                           "why" marker (because / so that / to / prevent / →, …).
#          This turns the "no empty commit messages — a correct prefix followed by
#          'update X' is still empty; say WHY" rule into a machine gate instead of a
#          self-reported judgement.
#
# Boundary (safety):
#   This script only CHECKS (read-only). It never edits git, hooks, or config.
#   Wiring it as a commit-msg hook (see SKILL.md) is a separate, manual step.
#
# Usage:
#   commit-convention-check.sh --msg "feat: ... (with a WHY)"
#   commit-convention-check.sh "feat: ... (with a WHY)"          # positional
#   commit-convention-check.sh --file .git/COMMIT_EDITMSG        # from a file
#   cat msg.txt | commit-convention-check.sh --stdin             # from stdin
#   commit-convention-check.sh --self-test                       # deterministic gate
#
# Extension:
#   CCC_EXTRA_PREFIX — comma- or whitespace-separated extra allowed prefixes,
#                      e.g. CCC_EXTRA_PREFIX="ops,wip". See examples/commit-convention-gate/.
#
# Exit codes:
#   0 = convention met (or skipped: merge/revert/fixup/squash)
#   1 = violation (prefix / not_empty / has_why failed)
#   2 = bad arguments
#
# Design: the check runs in python3 (standard library only) so length is counted in
#         Unicode code points — correct for multibyte languages, not just ASCII.
#         No jq dependency; BSD/macOS grep safe (no 'grep -P'). The self-test is
#         hermetic (built-in fixtures only; no git or external I/O).
# =============================================================
set -uo pipefail

# ---- python evaluator (argv[1] switches eval|selftest; eval reads CCC_MSG from env) ----
# `python3 -` reads its PROGRAM from stdin, so the message body is passed via the
# environment variable CCC_MSG (stdin is occupied by the heredoc = program source).
# One heredoc holds both modes so eval and self-test share the exact same check_msg.
py_run() {
  python3 - "$1" <<'PYEOF'
import sys, os, re

# --- convention definition ------------------------------------------------------
# Standard Conventional Commits types. Teams extend (never fork) via CCC_EXTRA_PREFIX.
ALLOWED_PREFIX = {
    "feat", "fix", "docs", "chore", "refactor",
    "test", "perf", "build", "ci", "style", "revert",
}
_extra = os.environ.get("CCC_EXTRA_PREFIX", "")
for tok in re.split(r"[,\s]+", _extra):
    tok = tok.strip().lower()
    if tok:
        ALLOWED_PREFIX.add(tok)

# Bare placeholders: a description that is ONLY one of these says nothing.
PLACEHOLDER_DESC = {
    "update", "updated", "updates", "change", "changes", "wip", "fix", "fixes",
    "tmp", "temp", "asdf", "misc", "minor", "stuff", "things", "fixup",
    # multibyte placeholders (kept so the gate is language-agnostic)
    "更新", "変更", "修正", "作業", "対応", "メモ",
}
# "why" markers: presence of any of these satisfies has_why for short descriptions.
WHY_MARKERS = [
    "because", "so that", "in order to", "prevent", "avoid", "enable",
    "fixes #", "closes #", "resolves #", "→",
    # multibyte markers (kept so the gate is language-agnostic)
    "ため", "理由", "なぜ", "回避", "防止", "解消", "により", "ように",
]

PREFIX_RE = re.compile(r"^([a-z]+)(\([^)]+\))?!?:\s+(.*)$")
SKIP_RE = re.compile(r"^(Merge |Revert |fixup!|squash!|Reapply )")


def first_content_line(text):
    """Return the first real line (skipping blank lines and # comments)."""
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        return s
    return ""


def check_msg(text):
    """Return (ok: bool, findings: list[str]). Pure — same input -> same output."""
    findings = []
    first = first_content_line(text)

    if not first:
        return False, ["empty message (no content line)"]

    # Auto-generated messages are out of scope -> skip (pass, fail-open).
    if SKIP_RE.match(first):
        return True, ["skipped: merge/revert/fixup/squash (not subject to the convention)"]

    m = PREFIX_RE.match(first)
    if m and m.group(1) in ALLOWED_PREFIX:
        prefix_ok = True
        desc = m.group(3).strip()
    else:
        prefix_ok = False
        desc = first

    dlen = len(desc)  # python3 str length = Unicode code points (multibyte-correct)
    core = re.sub(r"[\s.\-_、。！!？?]+", "", desc).lower()
    not_empty = dlen >= 6 and core not in PLACEHOLDER_DESC and desc.lower() not in PLACEHOLDER_DESC
    has_why = dlen >= 25 or any(w in desc for w in WHY_MARKERS)

    if not prefix_ok:
        allowed = "/".join(sorted(ALLOWED_PREFIX))
        findings.append(
            'prefix: no allowed type prefix ("type: description" form)\n'
            '    allowed: %s\n'
            '    got    : "%s"' % (allowed, first[:70])
        )
    if not not_empty:
        findings.append(
            'empty: description too short / bare placeholder (need >= 6 chars and not just "update"/"wip"/"fix")\n'
            '    got    : "%s" (%d chars)' % (desc[:70], dlen)
        )
    if not has_why:
        findings.append(
            'no-why: description < 25 chars and no "why" marker (add because/so that/in order to/prevent/avoid/→ etc.)\n'
            '    got    : "%s" (%d chars)' % (desc[:70], dlen)
        )

    return (prefix_ok and not_empty and has_why), findings


# ---------------------------------------------------------------------------
def run_eval():
    text = os.environ.get("CCC_MSG", "")
    ok, findings = check_msg(text)
    if ok:
        note = findings[0] if findings else "prefix/not_empty/has_why all pass"
        print("✅ commit convention PASS — %s" % note)
        return 0
    print("❌ commit convention FAIL — %d violation(s):" % len(findings))
    for f in findings:
        print("  • " + f)
    return 1


def run_selftest():
    print("── commit-convention-check self-test (hermetic; check_msg logic) ──")
    # (label, message, expected ok)
    cases = [
        # ---- pass (convention met) -------------------------------------
        ("prefix+why+long",
         "fix(api): retry on timeout to avoid dropped connections", True),
        ("english so that",
         "feat: add OAuth login so that users authenticate without a shared password", True),
        ("ends in update but has why",
         "docs: update README to prevent confusion about the rate-limit values", True),
        ("long text exempts why",
         "refactor: extract the retry backoff into a shared helper module", True),
        ("merge is skipped -> pass",
         "Merge branch 'main' into feature/x", True),
        ("short english but why marker (prevent)",
         "fix: guard nil to prevent panic", True),
        ("multibyte with why marker",
         "docs: 混乱を防ぐため設定値を明記した", True),
        # ---- fail (violation) ------------------------------------------
        ("prefix ok but bare placeholder",
         "docs: update", False),
        ("no prefix (empty phrase)",
         "update the config file", False),
        ("wip placeholder",
         "chore: wip", False),
        ("prefix ok, not placeholder, but why absent (<25)",
         "docs: update the config file", False),
        ("prefix ok but 2-char description",
         "feat: go", False),
        ("english placeholder + no prefix",
         "update stuff", False),
        ("empty message",
         "\n   \n# comment only\n", False),
        ("multibyte bare placeholder",
         "docs: 更新", False),
    ]
    fail = 0
    for label, msg, exp in cases:
        got, findings = check_msg(msg)
        mark = "✅" if got == exp else "❌"
        verdict = "PASS" if got else "FAIL"
        if got != exp:
            fail = 1
        print("  %s %-42s -> %s (expected %s)" % (
            mark, label, verdict, "PASS" if exp else "FAIL"))
    if fail == 0:
        print("✅ SELF-TEST PASS (logic healthy; all %d cases match)" % len(cases))
        return 0
    print("❌ SELF-TEST FAIL (judgement logic broken)")
    return 1


mode = sys.argv[1] if len(sys.argv) > 1 else "eval"
if mode == "selftest":
    sys.exit(run_selftest())
sys.exit(run_eval())
PYEOF
}

# ---- argument parsing --------------------------------------------------
MODE="eval"; MSG=""; FILE=""; SRC=""
while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) MODE="selftest"; shift ;;
    --msg|-m)    MSG="${2:-}"; SRC="msg"; shift 2 ;;
    --file|-f)   FILE="${2:-}"; SRC="file"; shift 2 ;;
    --stdin)     SRC="stdin"; shift ;;
    -h|--help)
      grep -E '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    --) shift; MSG="${1:-}"; SRC="msg"; shift || true ;;
    -*) echo "unknown arg: $1" >&2; exit 2 ;;
    *)  # positional argument = message body
        MSG="$1"; SRC="msg"; shift ;;
  esac
done

if [ "$MODE" = "selftest" ]; then
  py_run selftest
  exit $?
fi

# ---- resolve the message input (collapse to MSG -> pass via CCC_MSG env) ----
case "$SRC" in
  file)
    [ -n "$FILE" ] || { echo "usage: --file <path>" >&2; exit 2; }
    [ -f "$FILE" ] || { echo "no such file: $FILE" >&2; exit 2; }
    MSG="$(cat -- "$FILE")" ;;
  msg)
    : ;;  # MSG already set from args
  stdin)
    MSG="$(cat)" ;;
  *)
    # no arg and no file: read stdin if it is a pipe/redirect
    if [ ! -t 0 ]; then
      MSG="$(cat)"
    else
      echo "usage: commit-convention-check.sh --msg <text> | --file <path> | --stdin | --self-test" >&2
      exit 2
    fi ;;
esac

CCC_MSG="$MSG" py_run eval
exit $?
