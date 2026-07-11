# Example git pre-commit hooks

Deterministic **git** hooks (run by `git commit`, not by Claude Code) that stop bad
content from reaching history. They are the "secrets & size guard" and shell-lint layers
of [`docs/harness-profile.md`](../../docs/harness-profile.md).

| File | Blocks |
|---|---|
| `pre-commit-quality.sh` | committed secrets (AWS/OpenAI/Stripe/GitHub/Google/Slack/PEM…), files >1 MB, and `.env` files. Escape hatch: `# pragma: allowlist secret` on the line. |
| `pre-commit-shell-lint.sh` | BSD-incompatible `grep -P` / Perl escapes in staged shell, and `shellcheck --severity=error` (skips gracefully if shellcheck is absent). Escape hatch: `# pragma: allowlist bsd-grep`. |
| `pre-commit` | dispatcher git invokes; runs both and blocks the commit if either fails. |

These check the **staged** content (`git show :file`), so they see exactly what would be
committed — not your working-tree copy.

## Enable

Pick one wiring. Both work on macOS and Linux; neither touches `.git/hooks` you don't own.

```bash
# A) point git straight at this directory (simplest for trying it out)
git config core.hooksPath examples/git-hooks

# B) vendor into your own .githooks/ (recommended — lives with your repo)
mkdir -p .githooks
cp examples/git-hooks/pre-commit examples/git-hooks/pre-commit-*.sh .githooks/
chmod +x .githooks/pre-commit .githooks/pre-commit-*.sh
git config core.hooksPath .githooks
```

Commit `core.hooksPath` intent in your README so teammates run `git config core.hooksPath …`
after cloning (git does not do it automatically).

## Test

```bash
bash examples/git-hooks/test-git-hooks.sh
```

The self-test builds a throwaway git repo, stages fixtures (a fake secret, a `grep -P`
script, and clean files), and asserts each hook blocks the bad case and passes the good one.

## Notes

- **Commit-message rules belong in `commit-msg`, not `pre-commit`** — the message does not
  exist yet at pre-commit time.
- A gate that depends on one CLI (`shellcheck`, `jq`) must **degrade, not hard-fail**, when
  it is missing — otherwise a missing tool blocks every commit. `pre-commit-shell-lint.sh`
  skips shellcheck when absent for exactly this reason.
