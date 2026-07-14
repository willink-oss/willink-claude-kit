# Hooks guide — writing and testing Claude Code hooks safely

Hooks are shell commands Claude Code runs at defined lifecycle events. They are powerful
(a `PreToolUse` hook can block a tool call) and easy to get subtly wrong (silent failures,
non-portable `grep`, wrong fail policy). This guide consolidates the conventions the kit
follows; runnable templates live in [`examples/hooks/`](../examples/hooks/).

## 1. Input is JSON on STDIN — parse with `jq`

Hook input arrives as a JSON object on **stdin**, *not* as environment variables. Read it
and extract fields with `jq`:

```bash
input="$(cat)"
tool="$(printf '%s' "$input" | jq -r '.tool_name // empty')"
cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // empty')"
```

The payload shape depends on the event (e.g. `PreToolUse` carries `tool_name` /
`tool_input`; `Notification` carries `message`).

## 2. Fail-closed vs fail-open — pick by the hook's job

A hook *will* eventually hit an internal error (missing `jq`, unparseable input). What it
does then is a security decision, not an afterthought:

| Hook class | Events | On error | Block signal |
|---|---|---|---|
| **Security / gate** | `PreToolUse` | **fail-CLOSED** — `exit 2` (deny) | `exit 2` blocks the call; stderr is shown to Claude |
| **Advisory / notify** | `PostToolUse`, `Notification`, `Stop`, `SubagentStop`, `UserPromptSubmit`, `SessionStart`, `SessionEnd`, `PreCompact` | **fail-OPEN** — `exit 0` | never disrupt the session |

- Fail-closed means: on an *error* — missing `jq`, unparseable or empty stdin — **deny**
  (`exit 2`). A security hook that silently allows on error is worse than no hook. (A
  well-formed payload that simply isn't the call you gate — a non-Bash tool, or no
  command string — is allowed: there is nothing unsafe to deny.)
- Fail-open means: a notification hook must never break the session because, say,
  `notify-send` is missing. Swallow the error and `exit 0`.

`exit 0` = allow / success. `exit 2` = block (PreToolUse) with stderr surfaced to Claude.
Other non-zero codes are treated as a non-blocking error.

## 3. `grep` portability — macOS ships BSD grep

CI and Linux dev boxes have GNU grep; macOS ships **BSD grep**. Write patterns that run on
both:

- Use **POSIX ERE** via `grep -E`.
- **Do not** use `grep -P` (Perl mode) — unsupported on BSD grep.
- **Do not** use Perl escapes like `\s` `\d` `\w` — use POSIX classes: `[[:space:]]`,
  `[[:digit:]]`, `[[:alnum:]]`.

```bash
# portable: matches one-or-more spaces
grep -qE '[[:space:]]+'        # good
grep -qP '\s+'                 # BAD — fails on macOS
```

## 4. Self-test both a block case and a pass case

Every hook ships with a self-test that exercises **both** outcomes — the case it should
block *and* a case it should let through — plus its fail policy on malformed input. A hook
that only tests the happy path will silently rot into a rubber stamp. See
[`examples/hooks/test-hooks.sh`](../examples/hooks/test-hooks.sh); the kit runs it in CI
via `scripts/test/test_hooks.sh` on both Linux (GNU grep) and macOS (BSD grep).

## 5. Valid event names

Only these events exist. There is **no** `PreCommit` / `PostCommit` / `OnError`:

`PreToolUse` · `PostToolUse` · `UserPromptSubmit` · `Notification` · `Stop` ·
`SubagentStop` · `PreCompact` · `SessionStart` · `SessionEnd`

## Templates

Claude Code hooks (wire up in `.claude/settings.json` under `"hooks"` — each template's
header has the exact snippet):

| File | Event / class | Policy |
|---|---|---|
| [`examples/hooks/pretooluse-block-example.sh`](../examples/hooks/pretooluse-block-example.sh) | `PreToolUse` — teaching denylist | fail-closed (`exit 2`) |
| [`examples/hooks/pre-bash-safety.sh`](../examples/hooks/pre-bash-safety.sh) + [`_strip-command.awk`](../examples/hooks/_strip-command.awk) | `PreToolUse` Bash — production denylist (strips quoted/heredoc text first) | fail-closed |
| [`examples/hooks/pre-file-protect.sh`](../examples/hooks/pre-file-protect.sh) | `PreToolUse` Write/Edit — `.env`/key/`.git`/settings guard | fail-closed |
| [`examples/hooks/post-build-eval.sh`](../examples/hooks/post-build-eval.sh) | `PostToolUse` — test/lint/build failure evaluator | fail-open (`exit 0`) |
| [`examples/hooks/post-tool-log.sh`](../examples/hooks/post-tool-log.sh) | `PostToolUse` — JSONL tool-call log ("observe, then promote") | fail-open |
| [`examples/hooks/pre-compact-snapshot.sh`](../examples/hooks/pre-compact-snapshot.sh) | `PreCompact` — persist work state across compaction | fail-open |
| [`examples/hooks/notification-notify-example.sh`](../examples/hooks/notification-notify-example.sh) | `Notification` — desktop notify | fail-open |
| [`examples/hooks/test-hooks.sh`](../examples/hooks/test-hooks.sh) | self-test harness | block + pass cases |

**git** pre-commit hooks (run by `git commit`, not Claude Code — wire up with
`git config core.hooksPath …`) live in [`examples/git-hooks/`](../examples/git-hooks/):
a secrets/size/`.env` guard (`pre-commit-quality.sh`) and a BSD-grep + shellcheck gate
(`pre-commit-shell-lint.sh`). See its [README](../examples/git-hooks/README.md).

## 6. Hard-won lessons

Small traps that turn a hook into a silent liability:

- **A fail-closed hook must not depend on a single CLI.** If `jq` is missing and the hook
  blocks *everything* on error, one missing package freezes the whole tool. Degrade first
  (jq → python3), then fail closed only if no parser exists. See `pre-bash-safety.sh`.
- **Test write-behavior against a temp fixture, never a real checkout.** A hook that gates
  "no push to main" must be tested in a throwaway repo — "I'm on a feature branch right now"
  is not a controlled test, and a shared checkout's branch can change under you.
- **Commit-message rules belong in a `commit-msg` git hook, not `pre-commit`.** At
  pre-commit time the message does not exist yet.
- **Commit with path arguments** (`git commit -m … -- <paths>`) so a parallel session's
  staged files don't ride along in your commit.
- **Advisories belong on `UserPromptSubmit` (+ `additionalContext`), not `Stop`.** A `Stop`
  hook can only give feedback by *blocking*, so a false positive is expensive; injecting
  context on the next prompt is a gentler, reversible nudge.
- **Inline shell runs under the user's shell — mind word-splitting.** In zsh an unquoted
  `for x in $var` iterates the whole value as ONE word and silently misbehaves. Use a
  literal list or a quoted array; a `#!/usr/bin/env bash` script file is unaffected.
- **`PreCommit` / `PostCommit` / `OnError` are not events.** Only the names in §5 exist.
