#!/usr/bin/env bash
# =============================================================
# pre-commit-silent-zero.sh — 「空出力を 0 件と読む」構造を staged スクリプトで止める
#
# なぜ在るか（M1 T6・ミスログの gate 空欄から機械化した 1 本目）:
#
#   ミスログ 42 件のうち `silent-zero`（取得失敗・空出力を「0 件 / 異常なし」と読んだ）が
#   **3 件**あり、いずれもゲートが無かった。3 件は別々の事故に見えて構造が同じだった:
#
#     m-2026-06-11-01  gh が断続 401 を返したが `2>/dev/null` が握り潰し、
#                      実際 25 件 open だったものを「open PR 0 件」と 責任者 へ報告した
#     m-2026-07-19-01  フィルタが `routines/`（実際は `routine/`）でループが 0 回しか回らず、
#                      98 本の削除可能ブランチを数週間「0 件」と出し続けた
#     m-2026-09-08-03  grep が 0 件を返したのを「異常なし」と読んだ。実際は検索語が
#                      Unicode 分解形と合わない、そもそも当たらない語だった
#
#   共通するのは **「走査 0 件」を異常として扱っていない**こと。
#   1 回は事故だが 3 回目は設計の欠陥なので機械で止める（bug-pattern-ledger の基準）。
#
# 何を落とすか（staged の *.sh / *.py のみ・3 規則）:
#
#   [swallowed-fetch]      失敗しうる取得（gh / curl / aws / git ls-remote）を
#                          `2>/dev/null` 付きのコマンド置換で受け、exit code を見ていない
#   [unchecked-subprocess] python で subprocess を呼びながら、returncode を
#                          同じファイル内で一度も読んでいない
#   [zero-denominator]     `wc -l` / `grep -c` で件数を作りながら、その変数を
#                          **数と比べる分岐が 1 つも無い**（＝0 件が正常系）
#
# 逃げ道（意図的にそう書く場合）:
#   行末に `# silent-zero-ok: <理由>` を付ける。**理由を書かないと通らない**。
#   ファイル全体を免除する口は用意しない（免除は静かに恒久化するため）。
#
# Exit: 0 = 違反なし / 1 = 違反あり（commit を止める）
# =============================================================
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT" || exit 0

# staged なスクリプトだけを見る。テスト時は SILENT_ZERO_FILES で差し替える。
if [ -n "${SILENT_ZERO_FILES:-}" ]; then
  # shellcheck disable=SC2206
  FILES=($SILENT_ZERO_FILES)
else
  mapfile -t FILES < <(git diff --cached --name-only --diff-filter=ACM 2>/dev/null \
    | grep -E '\.(sh|py)$' || true)
fi

[ "${#FILES[@]}" -eq 0 ] && exit 0

findings=$(python3 - "${FILES[@]}" <<'PY'
import os
import re
import sys

Q = chr(34)  # `"` をソースに直書きしない（heredoc の入れ子でエスケープが壊れるため）

# 失敗しうる外部取得。ローカルで完結する ls / cat は対象にしない
FETCH = re.compile(r"\b(gh|curl|aws|supabase)\s|\bgit\s+ls-remote\b")
SWALLOW = re.compile(r"^\s*(?:local\s+)?([A-Za-z_][A-Za-z0-9_]*)=\$\((.*2>\s*/dev/null.*)\)")
GUARD = re.compile(r"\$\?|\|\||&&|\bif\s+!|\bset\s+-e\b|returncode|check=True")
COUNT = re.compile(r"^\s*(?:local\s+)?([A-Za-z_][A-Za-z0-9_]*)=\$\(.*(?:wc\s+-l|grep\s+-c).*\)")  # pragma: allowlist bsd-grep（grep でなく python の正規表現）
OK = re.compile(r"#\s*silent-zero-ok:\s*\S")
SUBPROC = re.compile(r"subprocess\.(run|Popen|check_output|call)\s*\(")
RETCODE = re.compile(r"\breturncode\b|\bcheck=True\b|CalledProcessError")


def considered(var, body):
    """件数変数が「数と比べられているか」を返す純関数。

    自己テストと 210 ファイルの実走査で 2 点直した（2026-09-09）:
      1. 引用符を許す。案内している直し方が `[ "$N" -eq 0 ]` なので、
         引用符を挟めないと **ゲートが自分の推奨手順を落とす**
      2. **0 以外の閾値との比較も「考慮済み」と数える**。`-ge 7` は件数を見ているのに
         リテラル 0 だけを探すと落ちる。29 件の指摘の大半がこれだった。
         過検出のゲートは使われなくなるので、ここは精度を採る
    """
    v = re.escape(var)
    pats = [
        r"\$\{?" + v + r"(?::-[^}]*)?\}?" + Q + r"?\s*"
        r"(?:-eq|-ne|-gt|-lt|-ge|-le|==|!=|>|<)\s*(?:[0-9]|" + Q + r"?\$)",
        r"\[\s*-[zn]\s+" + Q + r"?\$\{?" + v,
    ]
    return any(re.search(p, body) for p in pats)


out = []
for path in sys.argv[1:]:
    if not os.path.isfile(path):
        continue
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError):
        # 読めないファイルを「違反 0」と読まない（本ゲートが防ぐ当のもの）
        out.append("{}:0:[unreadable] 読めなかった（0 件ではなく不明）".format(path))
        continue

    body = "\n".join(lines)

    for i, line in enumerate(lines, 1):
        if OK.search(line):
            continue

        m = SWALLOW.match(line)
        if m and FETCH.search(m.group(2)):
            window = "\n".join(lines[i:i + 3])
            if not GUARD.search(window) and not GUARD.search(line):
                out.append("{}:{}:[swallowed-fetch] `{}` が stderr を捨てたまま "
                           "exit code を見ていない（空出力 != ゼロ件）"
                           .format(path, i, m.group(1)))

        c = COUNT.match(line)
        if c and not considered(c.group(1), body):
            out.append("{}:{}:[zero-denominator] `{}` は件数だが数と比べる分岐が "
                       "1 つも無い（走査 0 件が正常系になっている）"
                       .format(path, i, c.group(1)))

    if path.endswith(".py") and SUBPROC.search(body) and not RETCODE.search(body):
        line_no = next((i for i, l in enumerate(lines, 1) if SUBPROC.search(l)), 1)
        out.append("{}:{}:[unchecked-subprocess] subprocess を呼んでいるが "
                   "returncode を一度も読んでいない".format(path, line_no))

for line in out:
    print(line)
PY
)

if [ -z "$findings" ]; then
  exit 0
fi

n=$(printf '%s\n' "$findings" | grep -c . || true)  # silent-zero-ok: 直前の [ -z "$findings" ] で 0 件は除外済み
{
  echo "================================================"
  echo " [pre-commit-silent-zero] 走査 ${#FILES[@]} ファイル中 ${n} 件"
  echo "================================================"
  printf '%s\n' "$findings" | sed 's/^/  /'
  echo ""
  echo " 空出力・取得失敗を「0 件」と読む構造です。ミスログで 3 回再発しています。"
  echo ""
  echo " 直し方:"
  echo "   - 取得は exit code を見る。失敗は 0 件でなく ❓ 不明として出す"
  echo "   - 件数は 0 のとき異常として扱う分岐を書く（分母つきで出す）"
  echo "   - 意図的にそう書くなら行末に \`# silent-zero-ok: <理由>\` を付ける"
  echo "     （理由を書かないと通りません）"
  echo "================================================"
} >&2

exit 1
