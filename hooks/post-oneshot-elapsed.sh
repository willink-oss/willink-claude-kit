#!/usr/bin/env bash
# =============================================================
# post-oneshot-elapsed.sh — /oneshot の無人区間で、経過時間を予算と並べてモデルに見せる（PostToolUse）
#
# なぜ（2026-09-25・Opus 5.5 の手引き "Time signals for multiagent harnesses"）:
#   Opus 5.5 は経過時間の情報によく反応し、`elapsed 340s / 1200s` のような 1 行を毎回渡すと予算内に
#   収まるよう作業の配分（subagent の並べ方）を変える。予算は助言にすぎず、上限で止める仕組みではないので、
#   hard stop は既存の CAP（goal-loop の外側・budget.minutes の wall clock）のまま。
#   無人区間には人の prompt がほとんど来ないので UserPromptSubmit ではなく PostToolUse の
#   additionalContext（ツール結果の後にモデルへ見せる）で渡す。
#
# 何もしない（exit 0・出力なし）:
#   - oneshot/state.json が無い（/oneshot を使っていないリポ・セッションすべて）
#   - state.json / started_at が読めない（測れない時間を作らない）
#   - oneshot/STOP がある・status が delivered / escalated / stopped
#   - 前回出してから ONESHOT_ELAPSED_EVERY 秒（既定 60）経っていない（毎ツールでは出さない）
# 出す: {"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"oneshot: elapsed Ns / Ms"}}
#   budget.minutes が無ければ `elapsed Ns` だけ（予算を推測しない）。
#
# 最後に出した時刻は oneshot/.hooks.json（hook 専用）に持つ。state.json は周の書き手のもの。
# 同じファイルに hook 入力の transcript_path も記録する（2026-09-25・設計 §7-4）。oneshot-preflight.py が
# 本体と subagent の transcript からトークンを数えて budget.tokens の CAP に使う。出力と間隔の挙動は変えない。
# 仕様: https://code.claude.com/docs/en/hooks （PostToolUse decision control・2026-09-25 原文で確認）
# =============================================================
set -u
IN=$(cat 2>/dev/null) || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
PROG=''
read -r -d '' PROG <<'PY' || true
import json, os, sys, time, datetime

def load(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None

try:
    d = json.loads(sys.stdin.read() or "{}")
except Exception:
    sys.exit(0)
cands = [os.environ.get("ONESHOT_STATE", "")]
for base in (d.get("cwd") or "", os.environ.get("CLAUDE_PROJECT_DIR", "")):
    if base:
        cands.append(os.path.join(base, "oneshot", "state.json"))
state_path = next((p for p in cands if p and os.path.isfile(p)), "")
if not state_path:
    sys.exit(0)
st = load(state_path)
if not isinstance(st, dict):
    sys.exit(0)
odir = os.path.dirname(state_path)
if os.path.exists(os.path.join(odir, "STOP")) or str(st.get("status") or "") in ("delivered", "escalated", "stopped"):
    sys.exit(0)
sa = st.get("started_at")
try:
    if isinstance(sa, (int, float)):
        t0 = float(sa)
    else:
        t0 = datetime.datetime.fromisoformat(str(sa).replace("Z", "+00:00")).timestamp()
except Exception:
    sys.exit(0)
now = time.time()
sec = int(now - t0)
if sec < 0:
    sys.exit(0)

side_path = os.path.join(odir, ".hooks.json")
side = load(side_path)
if not isinstance(side, dict):
    side = {}
def save_side():
    try:
        tmp = side_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(side, fh, ensure_ascii=False)
        os.replace(tmp, side_path)
    except Exception:
        pass

tp = d.get("transcript_path")
tp_new = isinstance(tp, str) and tp != "" and side.get("transcript_path") != tp
if tp_new:
    side["transcript_path"] = tp
try:
    every = max(0, int(os.environ.get("ONESHOT_ELAPSED_EVERY", "60")))
except ValueError:
    every = 60
last = side.get("elapsed_last_emit")
if isinstance(last, (int, float)) and now - last < every:
    if tp_new:
        save_side()
    sys.exit(0)
side["elapsed_last_emit"] = now
save_side()

mins = (st.get("budget") or {}).get("minutes")
try:
    budget = int(float(mins) * 60) if mins else 0
except (TypeError, ValueError):
    budget = 0
msg = "oneshot: elapsed %ds / %ds" % (sec, budget) if budget else "oneshot: elapsed %ds" % sec
if budget and sec > budget:
    msg += "（予算を超えた。止めるのは goal-loop の外側の CAP minutes）"
sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}, ensure_ascii=False))
PY
printf '%s' "$IN" | python3 -c "$PROG" 2>/dev/null
exit 0
