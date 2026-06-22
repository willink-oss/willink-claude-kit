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

| File | Class | Policy |
|---|---|---|
| [`examples/hooks/pretooluse-block-example.sh`](../examples/hooks/pretooluse-block-example.sh) | `PreToolUse` security gate | fail-closed (`exit 2`) |
| [`examples/hooks/notification-notify-example.sh`](../examples/hooks/notification-notify-example.sh) | `Notification` advisory | fail-open (`exit 0`) |
| [`examples/hooks/test-hooks.sh`](../examples/hooks/test-hooks.sh) | self-test harness | block + pass cases |

Wire a hook up in `.claude/settings.json` under `"hooks"` (see each template's header for
the exact snippet).
