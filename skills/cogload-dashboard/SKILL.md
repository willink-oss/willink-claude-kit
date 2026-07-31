---
name: cogload-dashboard
description: Generate a single-pane operations dashboard that answers "what needs me right now?" by live-probing scattered state (a decision queue, open PRs, endpoints, scheduled work, cost) and folding away everything that does not need a human. Config-driven, self-contained HTML, no network at render time. A failed probe renders UNKNOWN, never 0. Triggers: cognitive load, dashboard, single pane, mission control, what needs me, agent ops, status overview, decision queue, operator overload.
allowed-tools: Bash, Read, Glob, Grep
---

# cogload-dashboard

An observation instrument for operators supervising many autonomous agents. It is
**not a gate** — a failing probe never fails the run, it renders as `unknown`.

## The problem it solves

When one person supervises many agents, the constraint stops being throughput and
becomes **attention allocation**. Four findings shape the design:

1. **Scrutiny inversely tracks trust.** Confidence that an AI can do the task is
   strongly *negatively* correlated with actually thinking critically about its
   output (CHI 2025, n=319, β=-0.69). Scrutiny cannot be left to willpower — the
   surface has to raise things at you.
2. **Sustained passive monitoring fails structurally** (vigilance decrement, ~75
   years of evidence). Monitoring must become *discrete, context-carrying decisions*.
3. **Self-report is badly miscalibrated.** METR's RCT found developers 19% slower
   while believing they were 20% faster. Show measured outcomes, never estimates.
4. **Cognitive work shifted** from doing to **verifying**, **integrating** and
   **stewarding** — so the page is organised by those, not by project or % complete.

A load-bearing negative result: three published ways to instrument cognitive load
itself (behavioural telemetry, questionnaire instruments, monitoring-rate metrics)
each failed adversarial verification. **This tool does not score your cognitive
load.** It shows outcome proxies and hides what does not need you.

## Use it when

- You are about to ask "what's the state of everything?" and the answer lives in
  five different files
- Before a standup, to get the current position rather than the recorded history
- An operator says they cannot tell what needs approving

## Do not use it when

- You need the detail of one project → use `pulse`
- You need to *change* state → this is read-only

## Run

```bash
python3 scripts/cogload-dashboard.py                 # measure → .cogload/dashboard.html
python3 scripts/cogload-dashboard.py --json          # snapshot to stdout
python3 scripts/cogload-dashboard.py --offline       # skip network probes
python3 scripts/cogload-dashboard.py --self-test     # hermetic, 29 checks
```

| exit | meaning |
|---|---|
| 0 | rendered |
| 1 | could not write output |
| 2 | usage or configuration error |
| 3 | **nothing could be measured** — distinct from 0 so a caller never reads "measured nothing" as "all clear" |

The HTML is self-contained (no external fonts, scripts or images) so it can be
published as an artifact or opened from disk. It is a **snapshot**: the page states
its own measurement time and claims nothing about the moment you read it.

## Configure

Zero config works — the local git tree is probed. To get value, put
`cogload.config.json` at the repo root. Full annotated example:
`examples/cogload/cogload.config.example.json`.

Lanes are the framework and are fixed; probes are what you plug in:

| lane | holds | fed by |
|---|---|---|
| 1 Verify | items needing a human decision | `decision_queue` (a markdown file) |
| 2 Integrate | live state to reconcile | `git`, `github_prs`, `http` probes |
| 3 Steward | policy, discipline, cost as a trend | `command`, `json_file`, `file_count` probes |

Probe types: `git`, `github_prs`, `http`, `command`, `json_file`, `file_count`.
`command` is the escape hatch — it runs anything and counts output lines.

## Display invariants — do not optimise these away

These are the whole point. Any change that breaks one makes the page lie.

- **A failed probe is `unknown`, never 0.** "Found nothing" and "checked nothing"
  are different facts and must never look alike.
- **Every count carries its denominator.** `9` is not a fact; `9 / 42 scanned` is.
- **A truncated query says so.** `gh search` silently caps at 30 by default — a cap
  reads as completeness unless it is announced.
- **Resolved items collapse.** Keep the active workspace small (context hygiene).
  Detail stays reachable behind `<details>`, never on the default view.
- **Nothing appears that was not measured** at the timestamp on the page.

## Lane 1 triage rule

An item is raised when it is urgent, **or** when it names a decision and is neither
waiting on someone else nor already resolved.

The two suppressions are deliberately **not applied to critical/high** items.
Mistaking "our move" for "waiting on them" silently parks real work for weeks, so
the rule errs toward over-surfacing where the cost of a miss is high.

When you change the triage rule, add a test on **both** sides — one that must be
raised and one that must be suppressed. A one-sided test lets the rule drift toward
silence, which is the failure you cannot see.

## Accessibility

Lanes are `<section aria-labelledby>` with real headings. Severity is carried by
**text**, not colour alone (WCAG 1.4.1) — the colour is redundant encoding. Metrics
are `<dl>`/`<dt>`/`<dd>` so each value is programmatically paired with its label.
Collapsing uses native `<details>`, so expanded/collapsed state is exposed for free.
Layout is rem-based with `minmax()` grids, so 200% zoom reflows without a horizontal
scrollbar. Verify changes with `python3 scripts/a11y-static-check.py --root <out-dir>`.

## Related

- `pulse` — live state of a single project
- `live-state-verify-guard` — catches status claims written without a probe behind them
