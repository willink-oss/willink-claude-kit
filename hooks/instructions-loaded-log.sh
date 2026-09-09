#!/usr/bin/env bash
# =============================================================
# instructions-loaded-log.sh — 常駐 context に「実際に何が載ったか」を記録する（常駐 context の予算管理）
#
# なぜ: 常駐の量は今まで「ファイルの行数」で推定していた。それは *置いてあるもの* であって
#       *載ったもの* ではない。paths: 付き rule は条件次第で載る/載らないが、
#       行数からは区別できない。InstructionsLoaded は載った実体を渡してくるので、
#       自己申告でなく実測で予算を管理できる（常駐 context の予算管理 の入力）。
#
# notification 系 = fail-open（exit 0）。記録に失敗してもセッションは止めない。
# 秘密は載せない（file_path とサイズのみ。中身は書かない）。
# =============================================================
set -u
LOG_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}/.claude/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || exit 0
IN=$(cat 2>/dev/null) || exit 0
printf '%s' "$IN" | python3 -c '
import sys, json, os, datetime
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
fp = d.get("file_path") or ""
# bytes と chars の両方を持つ。日本語は 1 字 ≈ 3 バイトなので、
# bytes から token を推すと 3 倍に外れる（2026-09-04 に実際に外した）。
size = chars = None
try:
    if fp and os.path.exists(fp):
        size = os.path.getsize(fp)
        chars = len(open(fp, encoding="utf-8", errors="ignore").read())
except Exception:
    pass
rec = {
    "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "file_path": fp,
    "memory_type": d.get("memory_type"),
    "load_reason": d.get("load_reason"),
    "bytes": size,          # None = 測れなかった（0 と混ぜない）
    "chars": chars,         # token 推定はこちらを使う（bytes ではない）
    "session_id": d.get("session_id"),
}
print(json.dumps(rec, ensure_ascii=False))
' >> "$LOG_DIR/instructions-loaded.jsonl" 2>/dev/null
exit 0
