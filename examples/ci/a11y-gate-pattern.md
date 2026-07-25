# CI pattern — the a11y gate as a ratchet

The gate is useful in CI only if it can be adopted on a repo that already has
violations. Blocking on "zero findings" on day one means the gate gets disabled on
day two. So the CI shape is a **ratchet**: the known set is frozen in a committed
baseline, new violations fail, and the baseline may only shrink.

> Wiring a required status check / branch protection is a **deliberate, higher-risk
> change**. Add the job first, watch it for a week, and only then make it required —
> by hand. The gate never installs itself (see the boundary in
> [`skills/a11y-static-gate/`](../../skills/a11y-static-gate/)).

## 1. Freeze the current state (once, in a PR)

```bash
python3 scripts/a11y-static-check.py --root . \
    --baseline .a11y-baseline.json --update-baseline
git add .a11y-baseline.json
```

Commit the baseline **with the finding counts in the PR body** so the starting debt is
on the record. A baseline created from an empty scan is refused (exit 3) — an empty
scan is not a clean repo.

## 2. Fail only on new violations

```yaml
# .github/workflows/a11y.yml
name: a11y

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  a11y-static:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v6
        with:
          python-version: "3.x"
      # The gate is stdlib-only python3: no npm install, no pub get, ~1s on a mid-size repo.
      - name: a11y static gate (ratchet)
        run: |
          python3 scripts/a11y-static-check.py \
            --root . --baseline .a11y-baseline.json
```

Exit codes the job relies on: `0` no new findings / `1` new findings / `2` usage or
missing baseline / `3` **UNKNOWN — zero files scanned** (a wrong path, not a clean
repo). Do not add `|| true`; that converts the gate into a decoration.

## 3. Keep the runtime tests separate

The static gate answers one question: *does every interactive element have a name, a
role and a state?* It cannot see layout overflow at large text sizes, focus order, or
whether a screen reader can actually complete a task. Those belong to the stack's own
test job:

```yaml
  a11y-runtime:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      # Flutter: the framework's own guideline matchers + a largest-Dynamic-Type pass
      # (template: examples/a11y/flutter/a11y_smoke_test.dart)
      - run: flutter test test/a11y_smoke_test.dart
      # Web: jsx-a11y runs inside `next lint`; axe-core runs against the rendered DOM
      # (setup: examples/a11y/web/eslint-a11y.config.md)
      - run: pnpm lint && pnpm test:a11y
```

## 4. Shrink the baseline, never grow it

```bash
# after fixing violations — prunes the fixed entries
python3 scripts/a11y-static-check.py --root . \
    --baseline .a11y-baseline.json --update-baseline
```

`--update-baseline` **refuses** to write a baseline with more errors than the previous
one. Growth requires `--allow-baseline-growth`, which exists so that the exception is
visible in the diff and has to be justified in the PR — the same discipline as
[`coverage-floor-lock`](../../skills/coverage-floor-lock/), where a lowered floor is
itself the finding.

A useful monthly metric: `counts.error` in the baseline. It should only ever go down.
