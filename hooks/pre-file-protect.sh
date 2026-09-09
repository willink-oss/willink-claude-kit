#!/bin/bash
# =============================================================
# pre-file-protect.sh — PreToolUse hook for Write/Edit tools
# Reads Claude Code hook JSON from stdin, blocks access to sensitive files.
# Exit: 0 = allow, 2 = block (fail-closed on parse error).
# =============================================================
set -uo pipefail

# --- Read hook JSON from stdin and extract file_path ---
# Parse .tool_input.file_path with jq when available; otherwise fall back to
# python3 (ships with macOS Command Line Tools). Failing closed when neither
# exists keeps this a fail-closed security hook while removing the hard
# single-point-of-failure on jq (a missing jq previously blocked ALL Write/Edit).
INPUT=$(cat)
if command -v jq >/dev/null 2>&1; then
  FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
elif command -v python3 >/dev/null 2>&1; then
  FILE_PATH=$(printf '%s' "$INPUT" | python3 -c 'import sys, json
try:
    data = json.load(sys.stdin)
    ti = data.get("tool_input") or {}
    sys.stdout.write(ti.get("file_path") or "")
except Exception:
    pass' 2>/dev/null)
else
  echo "BLOCKED: pre-file-protect.sh requires jq or python3 (neither installed)." >&2
  echo "Install: brew install jq" >&2
  exit 2
fi

if [ -z "$FILE_PATH" ]; then
  echo "BLOCKED: pre-file-protect.sh could not parse tool_input.file_path from hook stdin." >&2
  echo "This is a hook bug — check hook wiring in .claude/settings.json." >&2
  exit 2
fi

FILENAME=$(basename "$FILE_PATH")

block() {
  echo "BLOCKED: $1" >&2
  if [ "${2:-}" != "" ]; then
    echo "Alternative: $2" >&2
  fi
  exit 2
}

# --- Pattern 1: .env files (allow .env.example / .env.sample) ---
case "$FILENAME" in
  .env|.env.*)
    case "$FILENAME" in
      .env.example|.env.sample)
        : # allowed
        ;;
      *)
        block ".env files may contain secrets." "Edit .env.example (keys only, no values) and document in README."
        ;;
    esac
    ;;
esac

# --- Pattern 2: Credential / secret / key files (filename-based) ---
# Match by basename to avoid false positives like "secretary" in directory names.
# Case-insensitive shell globs via lowercased FILENAME.
FN_LOWER=$(printf '%s' "$FILENAME" | tr '[:upper:]' '[:lower:]')
case "$FN_LOWER" in
  credential|credentials|credential.*|credentials.*|credential-*|credentials-*|credential_*|credentials_*)
    block "Editing credential/secret/key files is prohibited." "Use AWS Secrets Manager, GitHub Secrets, or similar secure storage."
    ;;
  secret|secrets|secret.*|secrets.*|secret-*|secrets-*|secret_*|secrets_*)
    block "Editing credential/secret/key files is prohibited." "Use AWS Secrets Manager, GitHub Secrets, or similar secure storage."
    ;;
  id_rsa|id_rsa.*|id_ed25519|id_ed25519.*|id_ecdsa|id_ecdsa.*)
    block "Editing credential/secret/key files is prohibited." "Use AWS Secrets Manager, GitHub Secrets, or similar secure storage."
    ;;
  serviceaccountkey.json|firebase-adminsdk*.json)
    block "Editing credential/secret/key files is prohibited." "Use AWS Secrets Manager, GitHub Secrets, or similar secure storage."
    ;;
  *.pem|*.p12|*.pfx|*.key)
    block "Editing credential/secret/key files is prohibited." "Use AWS Secrets Manager, GitHub Secrets, or similar secure storage."
    ;;
esac

# --- Pattern 2a: 接頭辞つきクレデンシャル名（2026-07-19 追加）---
# 上の case は全て「先頭一致」のため、{サービス}-credentials.json のような
# 接頭辞つきの実在命名を取りこぼしていた（本リポの scripts/.freee-credentials.json
# が実際に素通りしていた）。ここでは basename の「部分一致」で拾う。
# ただし *secret* を素で部分一致させると secret-scan-audit.py や
# external-credentials-registry.md 等の正当なドキュメント/ツールまで巻き込むため、
# **実際に秘密を格納しうる拡張子に限定**する（.md / .py / .sh 等は対象外）。
# .example / .sample / .template は雛形なので明示的に許可する。
case "$FN_LOWER" in
  *.example|*.sample|*.template|*.md)
    : # 雛形・ドキュメントは対象外（.freee-credentials.json.example 等）
    ;;
  *credential*|*secret*|*apikey*|*api-key*|*api_key*|*token*)
    case "$FN_LOWER" in
      *.json|*.yaml|*.yml|*.txt|*.env|*.cfg|*.ini|*.conf|*.properties|*.toml)
        block "Editing credential/secret/key files is prohibited." "Use AWS Secrets Manager, GitHub Secrets, or similar secure storage."
        ;;
    esac
    ;;
esac

# --- Pattern 2c: direnv (.envrc) — .env と同族だが従来の .env* 判定から漏れていた ---
case "$FN_LOWER" in
  .envrc|.envrc.*)
    block "Editing .envrc (direnv) is prohibited — it commonly holds secrets." "Use .env.example for templates, or store secrets in a secret manager."
    ;;
esac

# --- Pattern 2b: .aws/credentials (path-level, filename-agnostic) ---
case "$FILE_PATH" in
  */.aws/credentials|*/.aws/credentials.*)
    block "Editing credential/secret/key files is prohibited." "Use AWS Secrets Manager, GitHub Secrets, or similar secure storage."
    ;;
esac

# --- Pattern 3: Harness self-modification prevention ---
case "$FILE_PATH" in
  *.claude/settings.json|*.claude/settings.local.json)
    block "Direct modification of .claude/settings.json is prohibited." "Use the /update-config skill or ask the 責任者 to edit manually."
    ;;
esac

# --- Pattern 4: .git internal files ---
case "$FILE_PATH" in
  */.git/config|*/.git/HEAD|*/.git/objects/*|*/.git/refs/*|*/.git/index)
    block "Direct modification of .git internals is prohibited." "Use git commands (git config, git update-ref, etc.)."
    ;;
esac

# --- Pattern 5: external-repository approval state (hook-owned) ---
case "$FILE_PATH" in
  */.claude/logs/external-write-approvals/*)
    block "External-repository approval state is hook-owned." \
          "Ask the user for a structured approval line in their next message."
    ;;
esac

# All checks passed — allow execution
exit 0
