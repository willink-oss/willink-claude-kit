# _strip-command.awk — helper for pre-bash-safety.sh
#
# Reads a shell command from stdin and writes a stripped version:
# - Heredoc bodies are removed (<<TAG ... TAG and <<'TAG' ... TAG)
# - Single-quoted string contents are removed ('...')
# - Double-quoted string contents are removed ("...")
#
# This is a best-effort lexer — it does not handle:
# - Escaped quotes inside strings (\" \' — rare in practice)
# - Nested command substitution inside quoted strings
#
# Trade-off: by stripping quoted content, destructive commands inside
# `bash -c "rm -rf /"` are NOT detected. This is a known, documented limitation
# (see pre-bash-safety.sh header) — the strip removes false positives from commit
# messages / docs at the cost of not seeing commands hidden inside string literals.

BEGIN {
  SQ = sprintf("%c", 39)  # single quote
  DQ = sprintf("%c", 34)  # double quote
  in_heredoc = 0
  tag = ""
  HEREDOC_RE = "<<-?[[:space:]]*[" DQ SQ "]?[A-Za-z_][A-Za-z0-9_]*[" DQ SQ "]?"
}

# Strip both single- and double-quoted string contents.
function strip_quoted(s,    result, i, c, state) {
  result = ""
  state = 0  # 0=outside, 1=inside single-quoted, 2=inside double-quoted
  for (i = 1; i <= length(s); i++) {
    c = substr(s, i, 1)
    if (state == 0) {
      if (c == SQ)      { state = 1 }
      else if (c == DQ) { state = 2 }
      else              { result = result c }
    } else if (state == 1) {
      if (c == SQ) { state = 0 }
    } else {  # state == 2
      if (c == DQ) { state = 0 }
    }
  }
  return result
}

{
  if (in_heredoc) {
    stripped = $0
    sub(/^[[:space:]]+/, "", stripped)
    if (stripped == tag) {
      in_heredoc = 0
      print strip_quoted($0)
    }
    # else: skip heredoc body content
    next
  }
  # Check for heredoc opening on this line
  if (match($0, HEREDOC_RE)) {
    matched = substr($0, RSTART, RLENGTH)
    sub(/^<<-?[[:space:]]*/, "", matched)
    gsub(DQ, "", matched)
    gsub(SQ, "", matched)
    tag = matched
    in_heredoc = 1
  }
  print strip_quoted($0)
}
