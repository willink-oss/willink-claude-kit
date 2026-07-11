# self-heal-ci — one fix cycle (headless prompt)

> `scripts/self-heal-ci.sh` loads this prompt via `claude -p` when it detects a red CI.
> One cycle = one fix. Whether CI is now green is decided by the wrapper (goal-loop's
> deterministic gate), never by this cycle self-reporting.

## Steps

1. **Identify the cause**: read the failing run with `gh run list -L1` then
   `gh run view <id> --log-failed`, and pin the single root cause of the red.
2. **Minimal fix**: change only what fixes that cause (no out-of-scope edits, no over-refactor).
   - CI config (workflow yaml) is wrong → fix that spot.
   - A source / test bug → fix that spot.
   - Cause is unclear or large → do not patch blindly; open an issue for human review and stop.
3. **Stop at PR**: cut a feature branch, commit the fix, and open a PR.
   - **Forbidden**: self-merge / tag push / pushing to main (master) / `git reset --hard` /
     force push.
   - Never commit secrets or `.env`.
4. Do exactly one fix (or one issue) per cycle. Re-verification is the wrapper's job next round.

## Stopping

- Once the fix is a PR, stop (the wrapper re-verifies whether CI went green).
- When the attempt cap is hit, the wrapper escalates (to stdout, or the `--escalate-file` sink)
  and stops.
