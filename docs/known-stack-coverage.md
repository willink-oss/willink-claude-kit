# Known stack coverage

> Status as of **v1.0** (2026-05-10). Updated each minor release.
> Source data: 14-day verification log across four i-Willink internal products
> (2026-04-26 → 2026-05-10).

This document records, per target stack, **what level of evidence** the kit's
behavior is supported by. It exists to help downstream adopters set
expectations: a "✅ verified" stack means the kit was driven end-to-end on
production-shaped tasks; an "🟡 install-only" stack means the kit installs
cleanly and shares no obvious incompatibilities, but no production-shaped
task has yet been driven through it.

---

## Coverage matrix (v1.0)

| Stack | Status | Reference repo | Evidence |
|---|---|---|---|
| Flutter + Firebase | ✅ verified | `tsuu` | Day 7 Task 1 (PR #54 dev-reviewer) + Task 3 (`/build` full flow). Findings parity vs manual-preload baseline: HIGH 100% / LOW 75%. MEMORY auto-write produced 3 entries inside `/build`. |
| Next.js + pnpm monorepo | ✅ verified | `fit-ai` | Day 7 Task 2 (4 dependabot PRs triaged in 1m31s, 49.4k tokens). Recommended merge order followed verbatim in same-day production merges. Coexists with existing `architecture-validator` / `security-reviewer` / `schema-sync-checker` / `test-generator`. |
| Flutter + Supabase | 🟡 install-only | `clubhouse` | Install verified during scaffold review. No `/agents` or `/build` invocation has been driven on a real PR yet (deferred from Day 7+ window due to time budget). |
| Next.js + Stripe | 🟡 install-only | `clublink-platform` | Same as above — installs cleanly, no end-to-end task driven yet. |

---

## What "verified" means here

A stack moves to **✅ verified** when *all* of the following hold for that
stack's reference repository:

1. `/agents` lists the four `dev-*` kit agents alongside any pre-existing
   project agents, without name or hook collisions.
2. At least one **task type** has been driven end-to-end (review and/or
   `/build` full flow). "End-to-end" includes phase observation, MEMORY-write
   observation, and recording of the agent's findings in
   `.claude/kit-verification/`.
3. **No fatal failure** was attributed to the kit during that task. Friction
   that fits in a docs PR is fine; "the kit broke the build / corrupted
   memory / overwrote unrelated files" is not.
4. At least one of the kit's own value claims (stack-agnostic baseline
   findings, batch triage, parallel verification, MEMORY auto-write) was
   *measurably* observed on this stack.

A stack at **🟡 install-only** has cleared step 1 but not step 2. It is safe
to adopt for purposes that do not require kit-side review/build value (for
example, sharing settings format, distributing baseline command set), but
the kit's review/build value on this stack is **not yet evidence-backed**.

---

## Promotion criteria (🟡 → ✅)

For each install-only stack, a single dogfood session is sufficient to
promote, provided that session produces a `.claude/kit-verification/<date>-<task>.md`
file with:

- `task type`, `kit version`, `mode` recorded
- `phase 別` observations (which phases ran / skipped, why)
- `計測値` (tool calls, tokens, time, verdict, MEMORY entries)
- `摩擦` and `価値` sections, one of each at minimum
- a concrete `upstream issue 候補` list, even if empty

The verification record is what graduates a stack — not the kit version
number, not adoption count.

---

## Promotion plan

### Flutter + Supabase (`clubhouse`)

- **Trigger**: Q3, when an open PR (or a small ad-hoc task) on `clubhouse`
  becomes a natural fit for `/agents dev-reviewer`.
- **Time cost**: ~60 minutes (one session).
- **Specific value to look for on this stack**: how kit's `dev-standards`
  treats Supabase Edge Function patterns, RLS policy review, and
  Postgres-side migrations vs Firebase patterns observed on `tsuu`.

### Next.js + Stripe (`clublink-platform`)

- **Trigger**: Q3, alongside any landing-page or pricing-section update.
- **Time cost**: ~60 minutes (one session).
- **Specific value to look for**: server-action / route-handler review
  surface vs the SSR-heavy fit-ai admin patterns; Stripe API key handling
  in environment configuration.

When both are promoted, the next minor release should reflect a fully
verified 4-stack matrix in this document.

---

## WordPress and PHP support level

Status: **📝 documented, unverified**.

WordPress + PHP is **not** an out-of-scope stack, but it is **not a verified
kit lane** either. The kit ships first-party guidance for it — agent stack
conventions (`dev-explorer`), quality-gate commands (`dev-tester`), coding
standards (`dev-standards`), and `docs/stack-specific-notes.md` — yet **no
production-shaped task has been driven through the kit on a WordPress repo**,
so there is no verification evidence behind that guidance.

**Support level a WordPress/PHP adopter can expect at v1.0:**

- ✅ The agents *load* WordPress/PHP conventions and `dev-tester` knows the
  `composer lint` / `phpstan` / PHPUnit gate sequence.
- ✅ Coding-standards and stack-notes guidance (security boundaries,
  `wp_unslash()` / `sanitize_*()` / nonce, version constraints) is documented.
- 🟡 None of the above is verified end-to-end. Treat it as **best-effort,
  unverified** — the same caveat as an `🟡 install-only` stack, plus the
  review surface itself is JS/TS-centric, so PHP-specific findings depth is
  lower than for a `✅ verified` lane.
- ❌ There is no near-term plan to promote WordPress/PHP to `✅ verified`; the
  kit's review surface intentionally stays JS/TS-centric.

This status is reviewed each minor release. Open a discussion if a real
adoption need would justify a verified PHP lane.

---

## Stacks explicitly out of scope at v1.0

The following stacks are intentionally **not** part of the v1.0 coverage
goal, ship **no** kit guidance, and have no promotion plan:

- **Static site generators (Hugo, Jekyll, etc.)** — too narrow a review
  surface to justify a kit lane.
- **Mobile-only repositories without Cloud Functions / backend** — would
  duplicate the existing `architecture-validator` patterns without adding
  kit-original value.

These exclusions are reviewed each minor release. Open a discussion if a
real adoption need surfaces.

---

## Related references

- 14-day verification log: i-willink-crew `assets/knowledge/2026-04-26-claude-kit-validation-log.md`
- Day 7 mid-term report: `assets/knowledge/2026-05-04-claude-kit-interim-report.md`
- v1.0 Go/No-Go report: `assets/knowledge/2026-05-10-claude-kit-validation-report.md`
- Verification protocol: `docs/verification-protocol.md`
