#!/bin/bash
# =============================================================
# pre-commit-shell-lint.sh — staged シェルスクリプトの lint gate
# 原則 P1 B4 (crew #761): 文書ルールだった 2 項目を機械化する。
#   1. BSD grep 非互換 (`grep -P` / grep 行の `\s` `\x27` Perl エスケープ)
#      → BLOCK（コメント行は除外・`# pragma: allowlist bsd-grep` で個別許可）
#   2. shellcheck --severity=error → BLOCK
#      （未インストール時は skip して通知のみ = fail-closed フックの
#        単一 CLI SPOF 回避・common-mistakes 準拠。brew install shellcheck 推奨）
#
# 対象: staged された *.sh と .githooks/* （staged 内容 = git show :file を検査）
# Exit: 0=PASS / 1=BLOCK
# Portable: macOS BSD grep compatible (no -P, no \s)
# =============================================================
set -uo pipefail

STAGED=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || true)
[ -z "$STAGED" ] && exit 0

# シェルスクリプトのみ抽出（*.sh + .githooks/ 直下）
SHELL_FILES=$(printf '%s\n' "$STAGED" | grep -E '\.sh$|^\.githooks/[^/]+$' || true)
[ -z "$SHELL_FILES" ] && exit 0

FAILED=0

# --- 1. BSD grep 互換チェック（決定論・依存 CLI なし） ---
while IFS= read -r f; do
  [ -z "$f" ] && continue
  CONTENT=$(git show ":$f" 2>/dev/null || true)
  [ -z "$CONTENT" ] && continue
  # コメント行と pragma 許可行を除いた実コード行のみ検査
  CODE=$(printf '%s\n' "$CONTENT" \
    | grep -vE '^[[:space:]]*#' \
    | grep -v 'pragma: allowlist bsd-grep' || true)

  # grep -P（結合フラグ -qP 等も検出）。単語境界を付け pgrep -P（正当）を誤検出しない
  VIOLATION=$(printf '%s\n' "$CODE" | grep -nE '(^|[^a-zA-Z0-9_])grep[[:space:]]+(-[a-zA-Z]+[[:space:]]+)*-[a-zA-Z]*P' || true)
  if [ -n "$VIOLATION" ]; then
    echo "❌ [$f] BSD 非互換: 'grep -P' は macOS で動きません（ERE + [[:space:]] を使う）" >&2 # pragma: allowlist bsd-grep
    printf '%s\n' "$VIOLATION" | head -3 >&2
    FAILED=1
  fi

  # grep 行の Perl エスケープ \s / \x27
  # ERE '\\s' = リテラル backslash + s（'\\\\s' だと backslash 2 個を要求してしまい
  # 実コードの \s を素通しする — /review 2026-07-03 HIGH 指摘の是正）
  VIOLATION=$(printf '%s\n' "$CODE" | grep -n 'grep' | grep -E '\\s|\\x27' || true) # pragma: allowlist bsd-grep
  if [ -n "$VIOLATION" ]; then
    echo "❌ [$f] BSD 非互換: grep パターンの Perl エスケープ（\\s → [[:space:]] / \\x27 → '\\''）" >&2 # pragma: allowlist bsd-grep
    printf '%s\n' "$VIOLATION" | head -3 >&2
    FAILED=1
  fi
done <<EOF_FILES
$SHELL_FILES
EOF_FILES

# --- 2. shellcheck（error 級のみ・未導入なら skip） ---
# Claude Code の shell は ~/.zprofile 経由で PATH を作るが、そこに Homebrew の
# shellenv 行が無いと /opt/homebrew/bin が載らない。その結果 shellcheck が
# 「インストール済なのに command -v で見つからない」→ gate が毎回 skip して
# 偽の緑を出し続けていた（2026-08-05 実測: 2026-07-03 導入分が全 commit で未実行）。
# skip 自体は許容だが、skip の理由を取り違えないよう PATH を先に補正する。
#
# SHELL_LINT_SKIP_PATH_FIX=1 で補正を無効化できる。これは test-hooks.sh の
# "no-shellcheck" モード専用の逃げ道で、BSD grep 検出器を shellcheck 抜きで
# 単体検証するために PATH を絞る意図を、この補正が打ち消さないようにするもの
# （補正を入れた結果テストが shellcheck 起因で偶然 BLOCK し、検出器の欠陥を
#  マスクしてしまうのを防ぐ。緑の理由が変わることを "緑のまま" 見逃さない）。
#
# 既知パスは macOS だけでなく Linux Homebrew も含める。2026-08-11 実測: EVO-X2
# (Linux) の brew は /home/linuxbrew/.linuxbrew/bin にあり、旧実装の 2 パスでは
# PATH 補正も「導入済だが解決できない」判定も届かず、shellcheck があるのに
# 「未インストール」と誤診して黙って skip し得た（＝ 2026-08-05 と同じ偽の緑）。
# 新しいホストを足すときは、この 1 か所に追加すれば補正と診断の両方に効く。
SHELL_LINT_KNOWN_BIN_DIRS="/opt/homebrew/bin /usr/local/bin /home/linuxbrew/.linuxbrew/bin $HOME/.linuxbrew/bin"

if [ "${SHELL_LINT_SKIP_PATH_FIX:-0}" != "1" ]; then
  for d in $SHELL_LINT_KNOWN_BIN_DIRS; do
    case ":$PATH:" in *":$d:"*) ;; *) [ -d "$d" ] && PATH="$d:$PATH" ;; esac
  done
  export PATH
fi

if command -v shellcheck >/dev/null 2>&1; then
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    TMP=$(mktemp /tmp/shell-lint.XXXXXX.sh)
    git show ":$f" > "$TMP" 2>/dev/null || { rm -f "$TMP"; continue; }
    if ! OUT=$(shellcheck --severity=error "$TMP" 2>&1); then
      echo "❌ [$f] shellcheck error:" >&2
      printf '%s\n' "$OUT" | grep -vE "^In /tmp/" | head -10 >&2
      FAILED=1
    fi
    rm -f "$TMP"
  done <<EOF_FILES2
$SHELL_FILES
EOF_FILES2
else
  # 「未インストール」と「PATH に無いだけ」を区別する。後者は環境の壊れであって
  # 正常な skip ではないため、直し方まで出す（黙って緑にしない）。
  FOUND_AT=""
  for d in $SHELL_LINT_KNOWN_BIN_DIRS; do
    [ -x "$d/shellcheck" ] && { FOUND_AT="$d"; break; }
  done
  if [ -n "$FOUND_AT" ]; then
    echo "❗ shellcheck は $FOUND_AT にあるが PATH から解決できず検査を skip した（偽の緑に注意）" >&2
    echo "   復旧: shell の rc に  eval \"\$($FOUND_AT/brew shellenv)\"  を追加して新セッション" >&2
  else
    echo "ℹ️  shellcheck 未インストールのため error 級検査を skip（brew install shellcheck 推奨・BSD grep 検査は実施済）" >&2
  fi
fi

exit $FAILED
