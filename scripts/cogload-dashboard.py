#!/usr/bin/env python3
"""cogload-dashboard.py — a config-driven cognitive-load dashboard for agent operations.

WHY THIS EXISTS

When one person supervises many autonomous agents, the bottleneck is not how much
the agents produce — it is how the operator's *attention* is allocated. Primary
research converges on four constraints that a supervision surface must respect:

  1. Scrutiny inversely tracks trust. Confidence that an AI can do the task is
     strongly NEGATIVELY correlated with actually thinking critically about its
     output (CHI 2025, n=319, beta=-0.69). You cannot leave scrutiny to willpower.
  2. Humans are structurally bad at sustained passive monitoring (vigilance
     decrement). Monitoring must become discrete, context-carrying decisions.
  3. Self-report is badly miscalibrated. METR's RCT found developers 19% SLOWER
     while believing they were 20% faster. Show measured outcomes, never estimates.
  4. Cognitive work has shifted from doing to VERIFYING, INTEGRATING and
     STEWARDING. Organise by those three, not by project or percent-complete.

Also load-bearing is a negative result: three proposed ways to turn cognitive load
itself into a KPI (behavioural telemetry, questionnaire instruments, monitoring-rate
instrumentation) were each refuted under adversarial verification. So this tool does
NOT score your cognitive load. It shows outcome proxies — backlog, staleness,
measurement coverage, cost — and hides everything that does not need you.

DESIGN INVARIANTS (do not "improve" these away)

  * A failed probe renders as UNKNOWN, never as 0. Zero findings and zero scanning
    are different facts and must never look alike.
  * Every count carries its denominator.
  * A truncated query is announced. Silent caps read as completeness.
  * Resolved items collapse. The active workspace stays small (context hygiene).
  * Nothing is displayed that was not measured at the timestamp shown on the page.

CONFIGURATION

Zero config works (the local git working tree is probed). To get the value, point
it at your decision queue and declare probes in `cogload.config.json` at the repo
root, or pass `--config <path>`. See `examples/cogload/cogload.config.example.json`.

usage:
  cogload-dashboard.py                    # measure, write HTML + snapshot JSON
  cogload-dashboard.py --json             # snapshot to stdout (for piping)
  cogload-dashboard.py --config <path>    # explicit config
  cogload-dashboard.py --out-dir <dir>    # output directory (default: .cogload/)
  cogload-dashboard.py --offline          # skip network probes (they render UNKNOWN)
  cogload-dashboard.py --self-test        # hermetic self-test, no network, no CLIs

exit codes:
  0 = rendered
  1 = could not write output
  2 = usage or configuration error
  3 = nothing could be measured (every probe UNKNOWN and no decision queue) —
      distinct from 0 so a caller never reads "measured nothing" as "all clear"

This is an observation instrument, not a gate. A failing probe does not fail the run.
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

DEFAULT_CONFIG_NAMES = ("cogload.config.json", ".cogload.json")
DEFAULT_OUT_DIR = ".cogload"

LANES = {
    1: ("Verify", "Items that need a human decision. Everything else is folded away."),
    2: ("Integrate", "Live-measured state. Probed at the timestamp above, not copied from docs."),
    3: ("Steward", "Policy, discipline and cost. Read as a trend, not as today's to-do."),
}

# A decision queue is a markdown file whose headings encode severity and topic.
# These defaults are deliberately generic; override them in config for your notation.
DEFAULT_SEVERITY_MARKERS = {
    "🔴": ["critical", "decide"],
    "🟠": ["high", "act"],
    "⛔": ["high", "act"],
    "🟡": ["warn", "watch"],
    "⚠️": ["warn", "watch"],
    "🆕": ["new", "new"],
    "🟢": ["info", "info"],
    "📌": ["info", "info"],
    "✅": ["done", "done"],
}
DEFAULT_DECISION_MARKERS = ["approve", "approval", "decide", "decision", "sign off", "blocked on"]
DEFAULT_EXTERNAL_WAIT_MARKERS = ["waiting on", "pending review by", "under review", "submitted to"]
DEFAULT_RESOLVED_MARKERS = ["✅", "approved", "shipped", "released", "landed", "done"]
SEVERITY_RANK = {"critical": 0, "high": 1, "warn": 2, "new": 3, "info": 4, "done": 5}
URGENT = ("critical", "high")


# --------------------------------------------------------------------- plumbing

class Probe:
    """One live measurement. Keeps failure distinguishable from emptiness."""

    def __init__(self, pid, label, lane, value=None, status="ok", detail="",
                 denominator=None, capped=False, rows=None):
        self.id = pid
        self.label = label
        self.lane = lane
        self.value = value
        self.status = status  # ok | partial | unknown
        self.detail = detail
        self.denominator = denominator
        self.capped = capped
        self.rows = rows or []

    def to_dict(self):
        return {k: getattr(self, k) for k in
                ("id", "label", "lane", "value", "status", "detail",
                 "denominator", "capped", "rows")}


def run(cmd, cwd=None, timeout=30):
    """Run a command, returning (rc, stdout, stderr). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0] if cmd else '?'}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except Exception as exc:
        return 1, "", str(exc)


def load_config(explicit, root):
    """Return (config, path, error). A missing config is not an error — defaults apply."""
    candidates = [explicit] if explicit else [os.path.join(root, n) for n in DEFAULT_CONFIG_NAMES]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh), path, ""
            except (OSError, json.JSONDecodeError) as exc:
                return None, path, f"{path}: {exc}"
    if explicit:
        return None, explicit, f"config not found: {explicit}"
    return {}, "", ""


# ---------------------------------------------------------------- decision queue

def parse_decision_queue(cfg, root):
    """Parse the markdown decision queue into severity-ranked items.

    Heading shape:  ## <severity marker> [<topic>] <title>
    Both the marker and the bracketed topic are optional.

    An item is raised to lane 1 when it is urgent, or when it names a decision and
    is neither waiting on someone else nor already resolved. The two suppressions
    are NOT applied to urgent items: mistaking "our move" for "waiting on them"
    silently parks real work, so this errs toward over-surfacing.
    """
    if not cfg:
        return None, "no decision_queue configured"
    path = cfg.get("path")
    if not path:
        return None, "decision_queue.path missing"
    full = path if os.path.isabs(path) else os.path.join(root, path)
    if not os.path.exists(full):
        return None, f"not found: {path}"

    severity_markers = cfg.get("severity_markers") or DEFAULT_SEVERITY_MARKERS
    decide = [m.lower() for m in cfg.get("decision_markers", DEFAULT_DECISION_MARKERS)]
    external = [m.lower() for m in cfg.get("external_wait_markers", DEFAULT_EXTERNAL_WAIT_MARKERS)]
    resolved = [m.lower() for m in cfg.get("resolved_markers", DEFAULT_RESOLVED_MARKERS)]
    prefix = "#" * int(cfg.get("heading_level", 2)) + " "

    try:
        with open(full, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        return None, str(exc)

    items = []
    for idx, line in enumerate(lines, start=1):
        if not line.startswith(prefix):
            continue
        heading = line[len(prefix):].strip()
        severity, label = "info", "info"
        for marker, spec in severity_markers.items():
            if heading.startswith(marker):
                severity = spec[0] if isinstance(spec, (list, tuple)) else str(spec)
                label = spec[1] if isinstance(spec, (list, tuple)) and len(spec) > 1 else severity
                heading = heading[len(marker):].strip()
                break
        m = re.match(r"^\[([^\]]+)\]\s*(.*)$", heading)
        topic, title = (m.group(1), m.group(2)) if m else ("", heading)
        hay = f"{topic} {title}".lower()
        suppressed = any(x in hay for x in external) or any(x in hay for x in resolved)
        needs = severity in URGENT or (any(x in hay for x in decide) and not suppressed)
        items.append({"line": idx, "severity": severity, "severity_label": label,
                      "topic": topic, "title": title.strip(),
                      "needs_decision": bool(needs) and severity != "done"})
    items.sort(key=lambda x: (SEVERITY_RANK.get(x["severity"], 9), x["line"]))
    return items, ""


# ----------------------------------------------------------------------- probes

def probe_git(spec, root, offline):
    label = spec.get("label", "Working tree")
    lane = int(spec.get("lane", 2))
    rc, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    if rc != 0:
        return Probe(spec["id"], label, lane, status="unknown", detail="not a git repository")
    _, dirty, _ = run(["git", "status", "--porcelain"], cwd=root)
    rc3, counts, _ = run(["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"], cwd=root)
    ahead = behind = None
    if rc3 == 0 and len(counts.split()) == 2:
        ahead, behind = (int(x) for x in counts.split())
    changed = len([l for l in dirty.splitlines() if l.strip()])
    detail = f"branch {branch}"
    if ahead is not None:
        detail += f" · ahead {ahead} / behind {behind}"
    return Probe(spec["id"], label, lane, value=changed, status="ok",
                 detail=detail + " · uncommitted files")


def probe_github_prs(spec, root, offline):
    """Open pull requests across owners, split into work that may need a human and bot noise."""
    label = spec.get("label", "Open PRs")
    lane = int(spec.get("lane", 2))
    if offline:
        return Probe(spec["id"], label, lane, status="unknown", detail="skipped (--offline)")
    owners = spec.get("owners") or []
    limit = int(spec.get("limit", 200))
    if not owners:
        rc, out, _ = run(["gh", "repo", "view", "--json", "owner"], cwd=root)
        if rc == 0 and out:
            try:
                owners = [json.loads(out)["owner"]["login"]]
            except (KeyError, json.JSONDecodeError):
                owners = []
    if not owners:
        return Probe(spec["id"], label, lane, status="unknown",
                     detail="no owners configured and current repo owner not resolvable")

    rows, scanned, failed, capped = [], 0, [], False
    for owner in owners:
        rc, out, err = run(["gh", "search", "prs", "--owner", owner, "--state", "open",
                            "--limit", str(limit), "--json",
                            "number,repository,author,isDraft,title,url"], cwd=root)
        scanned += 1
        if rc != 0:
            failed.append(f"{owner}: {err[:60] or f'rc={rc}'}")
            continue
        if not out:
            continue  # rc==0 with empty output is a genuine zero
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            failed.append(f"{owner}: unparseable response")
            continue
        if len(data) >= limit:
            capped = True
        for pr in data:
            author = (pr.get("author") or {}).get("login", "?")
            rows.append({
                "repo": (pr.get("repository") or {}).get("nameWithOwner", "?"),
                "number": pr.get("number"), "title": pr.get("title", ""),
                "url": pr.get("url", ""),
                "is_bot": author.endswith("[bot]") or author.lower().endswith("bot"),
                "is_draft": bool(pr.get("isDraft")),
            })
    if failed and not rows:
        return Probe(spec["id"], label, lane, status="unknown", denominator=scanned,
                     detail="every owner failed: " + "; ".join(failed))
    bots = sum(1 for r in rows if r["is_bot"])
    detail = f"{len(rows) - bots} human · {bots} bot · {sum(1 for r in rows if r['is_draft'])} draft"
    if failed:
        detail += f" — {len(failed)} owner(s) failed, count is a lower bound"
    return Probe(spec["id"], label, lane, value=len(rows),
                 status="partial" if failed else "ok", denominator=scanned,
                 detail=detail + f" (across {scanned} owner(s))", capped=capped)


def probe_http(spec, root, offline):
    """Liveness of endpoints. Reports status codes only — a 200 is not proof of correctness."""
    label = spec.get("label", "Endpoints")
    lane = int(spec.get("lane", 2))
    targets = spec.get("targets") or []
    if offline:
        return Probe(spec["id"], label, lane, status="unknown", detail="skipped (--offline)")
    if not targets:
        return Probe(spec["id"], label, lane, status="unknown", detail="no targets configured")
    rows, healthy = [], 0
    for t in targets:
        rc, out, err = run(["curl", "-o", "/dev/null", "-s", "-w", "%{http_code} %{time_total}",
                            "--max-time", str(spec.get("timeout", 10)), "-L", t["url"]], timeout=20)
        parts = out.split() if out else []
        code = int(parts[0]) if parts and parts[0].isdigit() else None
        ms = round(float(parts[1]) * 1000) if len(parts) > 1 else None
        ok = code is not None and 200 <= code < 400
        healthy += 1 if ok else 0
        rows.append({"name": t.get("name", t["url"]), "url": t["url"], "code": code,
                     "ms": ms, "status": "ok" if ok else ("unknown" if code is None else "error"),
                     "detail": "" if code is not None else (err[:50] or f"rc={rc}")})
    return Probe(spec["id"], label, lane, value=healthy, denominator=len(targets), status="ok",
                 detail="reachable (status code only — response body not validated)", rows=rows)


def probe_command(spec, root, offline):
    """Run a command and count its output lines. The generic escape hatch.

    A non-zero exit or an unparseable result yields UNKNOWN, never 0 — the whole
    point is that "the check did not run" must not look like "the check found nothing".
    """
    label = spec.get("label", spec["id"])
    lane = int(spec.get("lane", 3))
    cmd = spec.get("cmd")
    if not cmd:
        return Probe(spec["id"], label, lane, status="unknown", detail="no cmd configured")
    rc, out, err = run(cmd if isinstance(cmd, list) else ["sh", "-c", cmd], cwd=root,
                       timeout=int(spec.get("timeout", 30)))
    ok_codes = spec.get("ok_exit_codes", [0])
    if rc not in ok_codes:
        return Probe(spec["id"], label, lane, status="unknown",
                     detail=f"exit {rc}: {err[:60] or 'no stderr'}")
    lines = [l for l in out.splitlines() if l.strip()]
    pattern = spec.get("count_pattern")
    if pattern:
        m = re.search(pattern, out)
        if not m:
            return Probe(spec["id"], label, lane, status="unknown",
                         detail="count_pattern did not match the output")
        value = int(m.group(1))
    else:
        value = len(lines)
    return Probe(spec["id"], label, lane, value=value, status="ok",
                 detail=spec.get("detail", f"{len(lines)} line(s) of output"),
                 rows=[{"name": l[:120]} for l in lines[:10]])


def probe_json_file(spec, root, offline):
    """Read a numeric field out of a JSON state file.

    Supports the common "only meaningful if everything was sampled" case: when
    `unknown_key` reaches `denominator_key`, the value is reported as UNKNOWN rather
    than as its literal (usually 0) value.
    """
    label = spec.get("label", spec["id"])
    lane = int(spec.get("lane", 3))
    path = spec.get("path", "")
    full = path if os.path.isabs(path) else os.path.join(root, path)
    if not os.path.exists(full):
        return Probe(spec["id"], label, lane, status="unknown", detail=f"not found: {path}")
    try:
        with open(full, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return Probe(spec["id"], label, lane, status="unknown", detail=str(exc)[:70])
    value = data.get(spec.get("value_key", "value"))
    denom = data.get(spec.get("denominator_key", ""), None)
    unknown_n = data.get(spec.get("unknown_key", ""), None)
    if unknown_n is not None and denom and unknown_n >= denom:
        return Probe(spec["id"], label, lane, status="unknown", denominator=denom,
                     detail=f"{unknown_n}/{denom} samples never measured")
    detail = spec.get("detail", "")
    if unknown_n:
        detail = (detail + f" · {unknown_n}/{denom} samples unmeasured").strip(" ·")
    return Probe(spec["id"], label, lane, value=value, denominator=denom,
                 status="ok", detail=detail or "from state file")


def probe_file_count(spec, root, offline):
    """Count files matching a regex in a directory. Zero matches is an anomaly, not a zero.

    Name patterns drift silently (a directory gets renamed, a convention changes) and
    a confident "0" is the worst possible output, so an empty scan reports UNKNOWN.
    """
    label = spec.get("label", spec["id"])
    lane = int(spec.get("lane", 3))
    path = spec.get("path", "")
    full = path if os.path.isabs(path) else os.path.join(root, path)
    if not os.path.isdir(full):
        return Probe(spec["id"], label, lane, status="unknown", detail=f"no such directory: {path}")
    pattern = spec.get("pattern", r".*\.md$")
    try:
        names = [f for f in os.listdir(full) if re.match(pattern, f)]
    except OSError as exc:
        return Probe(spec["id"], label, lane, status="unknown", detail=str(exc)[:70])
    if not names:
        return Probe(spec["id"], label, lane, status="unknown", denominator=0,
                     detail=f"scanned {path} and matched nothing — has the convention changed?")
    return Probe(spec["id"], label, lane, value=len(names), denominator=len(names),
                 status="ok", detail=spec.get("detail", f"in {path}"))


PROBE_TYPES = {
    "git": probe_git,
    "github_prs": probe_github_prs,
    "http": probe_http,
    "command": probe_command,
    "json_file": probe_json_file,
    "file_count": probe_file_count,
}


# ------------------------------------------------------------------- snapshot

def build_snapshot(config, root, offline):
    now = datetime.now(timezone.utc).astimezone()
    items, queue_error = parse_decision_queue(config.get("decision_queue"), root)
    specs = config.get("probes")
    if specs is None:
        specs = [{"id": "git", "type": "git"}]  # zero-config default

    probes, errors = [], []
    for spec in specs:
        pid, ptype = spec.get("id"), spec.get("type")
        if not pid or ptype not in PROBE_TYPES:
            errors.append(f"skipped a probe: unknown type {ptype!r} (id={pid!r})")
            continue
        probes.append(PROBE_TYPES[ptype](spec, root, offline))

    active = [i for i in (items or []) if i["severity"] != "done"]
    done = [i for i in (items or []) if i["severity"] == "done"]
    decisions = [i for i in active if i["needs_decision"]]
    return {
        "title": config.get("title", "Cognitive Load Dashboard"),
        "measured_at": now.isoformat(),
        "measured_at_display": now.strftime("%Y-%m-%d %H:%M %Z"),
        "offline": offline,
        "config_errors": errors,
        "decision_queue": {
            "error": queue_error,
            "configured": items is not None,
            "decisions": decisions,
            "watch": [i for i in active if not i["needs_decision"]],
            "done_count": len(done),
            "total": len(items or []),
        },
        "probes": [p.to_dict() for p in probes],
    }


# ---------------------------------------------------------------------- render

def esc(x):
    return html.escape(str(x), quote=True)


def metric_tile(p):
    """One measurement. <dt>/<dd> pairs the label to the value programmatically."""
    if p["status"] == "unknown":
        shown, cls = "unknown", "unknown"
    else:
        shown, cls = esc(p["value"]), p["status"]
    denom = (f'<span class="denom"> / {esc(p["denominator"])}</span>'
             if p["denominator"] is not None and p["status"] != "unknown" else "")
    cap = '<span class="capped">truncated</span>' if p.get("capped") else ""
    return f"""      <div class="metric {cls}">
        <dt>{esc(p['label'])}</dt>
        <dd><span class="num">{shown}</span>{denom}</dd>
        <p class="detail">{esc(p['detail'])}{cap}</p>
      </div>"""


def queue_rows(items, show_source=True):
    out = []
    for i in items:
        topic = f'<span class="topic">{esc(i["topic"])}</span>' if i["topic"] else ""
        src = f'<span class="src">:{i["line"]}</span>' if show_source else ""
        out.append(f"""        <li class="row sev-{esc(i['severity'])}">
          <span class="pill">{esc(i['severity_label'])}</span>{topic}
          <span class="row-title">{esc(i['title'])}</span>{src}
        </li>""")
    return out


def render_html(snap):
    q = snap["decision_queue"]
    lanes = {1: [], 2: [], 3: []}
    for p in snap["probes"]:
        lanes.setdefault(int(p["lane"]), []).append(p)

    if not q["configured"]:
        headline_num, headline_txt = "—", (
            f"No decision queue configured ({esc(q['error'])}). "
            "Point <code>decision_queue.path</code> at your queue to use lane 1.")
    else:
        headline_num = len(q["decisions"])
        headline_txt = ("Items waiting on <strong>your</strong> decision. "
                        "This is a snapshot — nothing after the timestamp is included.")

    lane_html = []
    for lane_no in (1, 2, 3):
        name, note = LANES[lane_no]
        body = []
        if lane_no == 1:
            rows = queue_rows(q["decisions"]) if q["configured"] else []
            body.append('    <ul class="rows">\n' + ("\n".join(rows) if rows else
                        '        <li class="row empty">Nothing is waiting on you'
                        + (' (measured, not merely unscanned).' if q["configured"]
                           else ' — lane not configured.') + '</li>') + "\n    </ul>")
            if q["configured"]:
                watch = queue_rows(q["watch"])
                body.append(f"""    <details>
      <summary>{len(watch)} watching · {q['done_count']} resolved — folded away</summary>
      <ul class="rows">
{chr(10).join(watch) if watch else '        <li class="row empty">Nothing.</li>'}
      </ul>
    </details>""")
        probes = lanes.get(lane_no, [])
        if probes:
            body.append('    <dl class="metrics">\n' +
                        "\n".join(metric_tile(p) for p in probes) + "\n    </dl>")
            detail_rows = []
            for p in probes:
                for r in p.get("rows") or []:
                    status = r.get("status", "ok")
                    badge = r.get("code") if r.get("code") is not None else (
                        "?" if "code" in r else "")
                    extra = f'<span class="src">{esc(r["ms"])} ms</span>' if r.get("ms") else ""
                    detail_rows.append(f"""        <li class="row st-{esc(status)}">
          {f'<span class="pill">{esc(badge)}</span>' if badge != "" else ""}
          <span class="row-title">{esc(r.get('name', ''))}</span>{extra}
        </li>""")
            if detail_rows:
                body.append('    <ul class="rows">\n' + "\n".join(detail_rows) + "\n    </ul>")
        if not body:
            body.append('    <p class="empty">No probes assigned to this lane.</p>')
        lane_html.append(f"""  <section aria-labelledby="lane-{lane_no}">
    <h2 id="lane-{lane_no}">{esc(name)}</h2>
    <p class="lane-note">{esc(note)}</p>
{chr(10).join(body)}
  </section>""")

    warn = ""
    if snap["offline"]:
        warn += '<p class="warn-note">Offline run — network probes were not executed.</p>'
    for e in snap["config_errors"]:
        warn += f'<p class="warn-note">{esc(e)}</p>'

    return f"""<title>{esc(snap['title'])}</title>
<style>
:root {{
  color-scheme: light dark;
  --ground:#f7f6f3; --surface:#fff; --ink:#1c1c22; --ink-soft:#5a5a68; --rule:#dedbd4;
  --accent:#4a5a8c; --critical:#a8322c; --high:#b5651d; --warn:#8a6a14; --ok:#3d6b4f;
  --unknown:#6b6b76; --shadow:0 1px 2px rgba(28,28,34,.06);
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --ground:#16161a; --surface:#1e1e24; --ink:#ecebe8; --ink-soft:#a3a3ae; --rule:#33333d;
    --accent:#93a3d6; --critical:#e8817a; --high:#dda765; --warn:#d4b45e; --ok:#7fb894;
    --unknown:#9a9aa6; --shadow:none;
  }}
}}
:root[data-theme="dark"] {{
  --ground:#16161a; --surface:#1e1e24; --ink:#ecebe8; --ink-soft:#a3a3ae; --rule:#33333d;
  --accent:#93a3d6; --critical:#e8817a; --high:#dda765; --warn:#d4b45e; --ok:#7fb894;
  --unknown:#9a9aa6; --shadow:none;
}}
:root[data-theme="light"] {{
  --ground:#f7f6f3; --surface:#fff; --ink:#1c1c22; --ink-soft:#5a5a68; --rule:#dedbd4;
  --accent:#4a5a8c; --critical:#a8322c; --high:#b5651d; --warn:#8a6a14; --ok:#3d6b4f;
  --unknown:#6b6b76; --shadow:0 1px 2px rgba(28,28,34,.06);
}}
*{{box-sizing:border-box}}
body{{margin:0;padding:clamp(1rem,3vw,2.5rem);background:var(--ground);color:var(--ink);
 font-family:ui-sans-serif,-apple-system,"Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif;
 line-height:1.65;font-size:16px}}
.wrap{{max-width:78rem;margin:0 auto;display:flex;flex-direction:column;gap:1.75rem}}
.masthead{{display:flex;flex-wrap:wrap;align-items:baseline;gap:.5rem 1.25rem;
 border-bottom:2px solid var(--ink);padding-bottom:.85rem}}
.masthead h1{{margin:0;font-size:1.35rem;font-weight:650;letter-spacing:.02em}}
.measured{{font-family:ui-monospace,Menlo,monospace;font-size:.8rem;color:var(--ink-soft);
 font-variant-numeric:tabular-nums}}
.headline{{background:var(--surface);border:1px solid var(--rule);border-left:5px solid var(--accent);
 border-radius:3px;padding:1.1rem 1.35rem;box-shadow:var(--shadow)}}
.headline .big{{font-size:clamp(2.2rem,7vw,3rem);font-weight:680;line-height:1;
 font-variant-numeric:tabular-nums}}
.headline p{{margin:.45rem 0 0;color:var(--ink-soft);font-size:.92rem}}
section{{background:var(--surface);border:1px solid var(--rule);border-radius:3px;
 padding:1.15rem 1.35rem 1.35rem;box-shadow:var(--shadow)}}
section>h2{{margin:0 0 .3rem;font-size:1.02rem;font-weight:640;letter-spacing:.03em}}
.lane-note{{margin:0 0 1rem;color:var(--ink-soft);font-size:.84rem}}
dl.metrics{{display:grid;gap:.75rem;margin:0 0 1rem;
 grid-template-columns:repeat(auto-fit,minmax(11rem,1fr))}}
.metric{{border:1px solid var(--rule);border-radius:3px;padding:.7rem .85rem}}
.metric dt{{font-size:.78rem;color:var(--ink-soft);letter-spacing:.04em}}
.metric dd{{margin:.15rem 0 0;font-size:1.5rem;font-weight:640;line-height:1.2;
 font-variant-numeric:tabular-nums}}
.metric .denom{{font-size:.95rem;font-weight:400;color:var(--ink-soft)}}
.metric .detail{{margin:.3rem 0 0;font-size:.76rem;color:var(--ink-soft)}}
.metric.unknown dd{{color:var(--unknown);font-size:1.1rem}}
.capped{{color:var(--high);margin-left:.4rem}}
ul.rows{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}}
.row{{display:flex;flex-wrap:wrap;align-items:baseline;gap:.5rem;padding:.5rem 0 .5rem .7rem;
 border-top:1px solid var(--rule);border-left:3px solid transparent}}
.row:first-child{{border-top:none}}
.sev-critical{{border-left-color:var(--critical)}} .sev-high{{border-left-color:var(--high)}}
.sev-warn{{border-left-color:var(--warn)}} .sev-new,.sev-info{{border-left-color:var(--rule)}}
.st-ok{{border-left-color:var(--ok)}} .st-error,.st-unknown{{border-left-color:var(--unknown)}}
.pill{{font-size:.72rem;letter-spacing:.05em;padding:.12rem .5rem;border-radius:2px;
 border:1px solid currentColor;white-space:nowrap;font-variant-numeric:tabular-nums}}
.sev-critical .pill{{color:var(--critical)}} .sev-high .pill{{color:var(--high)}}
.sev-warn .pill{{color:var(--warn)}} .sev-new .pill,.sev-info .pill{{color:var(--ink-soft)}}
.st-ok .pill{{color:var(--ok)}} .st-error .pill,.st-unknown .pill{{color:var(--unknown)}}
.topic{{font-family:ui-monospace,Menlo,monospace;font-size:.74rem;color:var(--accent)}}
.row-title{{flex:1 1 20rem;min-width:0}}
.src{{font-family:ui-monospace,Menlo,monospace;font-size:.72rem;color:var(--ink-soft);
 font-variant-numeric:tabular-nums}}
.row.empty,.empty{{color:var(--ink-soft);font-size:.88rem}}
.warn-note{{margin:0 0 .8rem;font-size:.82rem;color:var(--high)}}
details{{margin-top:1rem;border-top:1px solid var(--rule);padding-top:.8rem}}
summary{{cursor:pointer;font-size:.86rem;color:var(--ink-soft)}}
summary:focus-visible,a:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
footer{{font-size:.78rem;color:var(--ink-soft);border-top:1px solid var(--rule);padding-top:.9rem}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}
</style>

<div class="wrap">
  <header class="masthead">
    <h1>{esc(snap['title'])}</h1>
    <span class="measured">measured {esc(snap['measured_at_display'])}</span>
  </header>

  <div class="headline">
    <div class="big">{headline_num}</div>
    <p>{headline_txt}</p>
  </div>
  {warn}
{chr(10).join(lane_html)}

  <footer>
    <p>Every number here came from a probe run at the timestamp above. A probe that
    failed reads <strong>unknown</strong>, never 0 — &ldquo;found nothing&rdquo; and
    &ldquo;checked nothing&rdquo; are different facts. Counts carry their denominator and a
    truncated query says so.</p>
    <p>This dashboard does not score your cognitive load: the three published ways to
    instrument it directly did not survive adversarial verification. It shows outcome
    proxies instead, and hides what does not need you.</p>
  </footer>
</div>
"""


# -------------------------------------------------------------------- self-test

def self_test():
    import tempfile
    failures, n = [], [0]

    def check(name, cond, extra=""):
        n[0] += 1
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {extra}"))
        if not cond:
            failures.append(name)

    root = tempfile.mkdtemp()
    queue = os.path.join(root, "queue.md")
    with open(queue, "w", encoding="utf-8") as fh:
        fh.write("""# Queue

## 🔴 [billing] Pricing change needs approval
## ✅ [infra] Migration shipped
## 🟡 [store] Waiting on review by the app store
## 🔴 [store] Waiting on review by the app store
## 🟢 [docs] Approved and shipped last week
## 🟡 [ops] Decision needed on retention window
""")
    cfg = {"decision_queue": {"path": queue}}
    items, err = parse_decision_queue(cfg["decision_queue"], root)
    check("parses the decision queue", err == "" and items is not None, err)
    check("reads every heading", len(items or []) == 6, f"got {len(items or [])}")
    check("ranks by severity", items[0]["severity"] == "critical")
    check("extracts the topic", items[0]["topic"] == "billing")
    by = {i["title"]: i for i in items}
    check("raises an explicit approval", by["Pricing change needs approval"]["needs_decision"])
    check("never raises a resolved item",
          all(not i["needs_decision"] for i in items if i["severity"] == "done"))
    check("drops work that is waiting on someone else",
          by["Waiting on review by the app store"]["needs_decision"] is False)
    check("keeps urgent items even when they look external",
          [i for i in items if i["severity"] == "critical"
           and i["topic"] == "store"][0]["needs_decision"] is True)
    check("does not re-raise something already approved",
          by["Approved and shipped last week"]["needs_decision"] is False)
    check("raises a plainly worded decision",
          by["Decision needed on retention window"]["needs_decision"])

    missing, err2 = parse_decision_queue({"path": "nope.md"}, root)
    check("a missing queue reports why", missing is None and "not found" in err2)
    none_cfg, err3 = parse_decision_queue(None, root)
    check("no queue configured is not an error state", none_cfg is None and err3 != "")

    # UNKNOWN must never be renderable as 0.
    p = probe_github_prs({"id": "prs"}, root, offline=True)
    check("offline PR probe is unknown", p.status == "unknown" and p.value is None)
    tile = metric_tile(p.to_dict())
    check("an unknown probe renders as 'unknown', not 0", "unknown" in tile and ">0<" not in tile)
    ok_tile = metric_tile(Probe("x", "L", 2, value=3, denominator=9, detail="d").to_dict())
    check("counts render with their denominator", "/ 9" in ok_tile)
    capped = metric_tile(Probe("x", "L", 2, value=200, capped=True, detail="d").to_dict())
    check("a truncated query is announced", "truncated" in capped)

    empty_dir = os.path.join(root, "adr")
    os.makedirs(empty_dir, exist_ok=True)
    fc = probe_file_count({"id": "adr", "path": empty_dir}, root, False)
    check("an empty scan is unknown, not zero", fc.status == "unknown" and fc.value is None)
    with open(os.path.join(empty_dir, "001-x.md"), "w") as fh:
        fh.write("x")
    fc2 = probe_file_count({"id": "adr", "path": empty_dir, "pattern": r"^\d+-.*\.md$"}, root, False)
    check("a non-empty scan counts", fc2.status == "ok" and fc2.value == 1)

    st = os.path.join(root, "state.json")
    with open(st, "w") as fh:
        json.dump({"cost": 0, "runs": 6, "unmeasured": 6}, fh)
    jp = probe_json_file({"id": "c", "path": st, "value_key": "cost",
                          "denominator_key": "runs", "unknown_key": "unmeasured"}, root, False)
    check("a fully unsampled metric is unknown, not 0", jp.status == "unknown")
    with open(st, "w") as fh:
        json.dump({"cost": 12, "runs": 6, "unmeasured": 0}, fh)
    jp2 = probe_json_file({"id": "c", "path": st, "value_key": "cost",
                           "denominator_key": "runs", "unknown_key": "unmeasured"}, root, False)
    check("a sampled metric reports its value", jp2.status == "ok" and jp2.value == 12)

    cp = probe_command({"id": "z", "cmd": ["false"]}, root, False)
    check("a failing command is unknown, not 0", cp.status == "unknown")

    snap = build_snapshot({"title": "T", "decision_queue": {"path": queue},
                           "probes": [{"id": "git", "type": "git"},
                                      {"id": "bad", "type": "nope"}]}, root, offline=True)
    check("an unknown probe type is reported, not silently dropped",
          len(snap["config_errors"]) == 1)
    out = render_html(snap)
    check("renders HTML", "<title>" in out and 'id="lane-1"' in out)
    check("names every lane via aria-labelledby", out.count("aria-labelledby=") == 3)
    check("ships no external resources", "http://" not in out and "//cdn" not in out)
    check("styles both themes",
          "prefers-color-scheme: dark" in out and '[data-theme="light"]' in out)
    check("escapes HTML in content",
          "&lt;" in render_html({**snap, "title": "<script>x</script>"}))

    empty = build_snapshot({}, root, offline=True)
    check("zero config still produces a page", "<title>" in render_html(empty))
    check("zero config says lane 1 is unconfigured",
          "not configured" in render_html(empty))

    print()
    if failures:
        print(f"SELF-TEST FAILED: {len(failures)} of {n[0]} — {', '.join(failures)}")
        return 1
    print(f"SELF-TEST PASSED: {n[0]} checks")
    return 0


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="Config-driven cognitive-load dashboard.")
    ap.add_argument("--config", help="path to cogload.config.json")
    ap.add_argument("--root", default=".", help="repository root (default: cwd)")
    ap.add_argument("--out-dir", default=None, help=f"output directory (default: {DEFAULT_OUT_DIR})")
    ap.add_argument("--json", action="store_true", help="write the snapshot to stdout")
    ap.add_argument("--offline", action="store_true", help="skip network probes")
    ap.add_argument("--self-test", action="store_true", help="hermetic self-test")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    root = os.path.abspath(args.root)
    config, cfg_path, err = load_config(args.config, root)
    if err:
        print(f"configuration error: {err}", file=sys.stderr)
        return 2

    snap = build_snapshot(config, root, args.offline)

    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
        return 0

    out_dir = args.out_dir or config.get("out_dir") or os.path.join(root, DEFAULT_OUT_DIR)
    html_path = os.path.join(out_dir, "dashboard.html")
    snap_path = os.path.join(out_dir, "snapshot.json")
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(render_html(snap))
        with open(snap_path, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"could not write output: {exc}", file=sys.stderr)
        return 1

    q = snap["decision_queue"]
    unknown = [p["id"] for p in snap["probes"] if p["status"] == "unknown"]
    print(f"measured {snap['measured_at_display']}" + (f"  (config: {cfg_path})" if cfg_path else
                                                       "  (no config — git only)"))
    if q["configured"]:
        print(f"  {len(q['decisions'])} awaiting you / {q['total']} headings"
              f"  ({q['done_count']} resolved folded away)")
    else:
        print(f"  decision queue: not configured ({q['error']})")
    # A partially-failed probe reports a LOWER BOUND. Dropping it from this line lets a
    # caller copy the number out as if it were the measured total.
    partial = [p["id"] for p in snap["probes"] if p["status"] == "partial"]
    print(f"  probes: {len(snap['probes']) - len(unknown)}/{len(snap['probes'])} measured"
          + (f", unknown: {', '.join(unknown)}" if unknown else ""))
    if partial:
        print(f"  ! partial: {', '.join(partial)} — these values are lower bounds, not totals")
    print(f"  {html_path}\n  {snap_path}")

    measured_anything = q["configured"] or any(p["status"] != "unknown" for p in snap["probes"])
    return 0 if measured_anything else 3


if __name__ == "__main__":
    sys.exit(main())
