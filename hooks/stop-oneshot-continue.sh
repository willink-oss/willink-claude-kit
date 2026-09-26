#!/usr/bin/env bash
# =============================================================
# stop-oneshot-continue.sh — /oneshot の無人区間で「報告して止まった」を完了と取り違えない（Stop hook）
#
# なぜ（2026-09-25・Opus 5.5 の手引き "Unattended agentic runs"）:
#   Opus 5.5 は長い作業の途中で進み具合を書き、そのままテキストで手番を終える（stop_reason=end_turn）
#   ことがある。無人のループはそれを完了と扱って止まる。手引きの推奨は「テキストで終わった手番は報告で
#   あって完了の証拠ではない。未完了の項目を名指しして続けさせ、同じタスクへの自動続行は 2〜3 回で打ち切る」。
#   /oneshot の完了は goal-loop の --check の exit code だけなので（自己申告を採点に使わない）、
#   oneshot/state.json の dod に緑でない項目が残っていれば止まらせない。
#
# 何もしない（exit 0・出力なし）:
#   - oneshot/state.json が無い（/oneshot を使っていないリポ・セッションすべて）
#   - 入力や state.json が読めない（fail-open。測れないものを理由に止めない）
#   - background_tasks / session_crons がある（完了ではなく「起こされ待ち」。Claude Code が起こし直すので
#     ここでは止めさせも続けさせもせず、続行回数も使わない）
# 止まってよい（exit 0）:
#   - oneshot/STOP がある（人の割り込み）・status が delivered / escalated / stopped・blocker に 1 行ある
#   - 同じ未完了項目のまま自動続行が上限（既定 3・ONESHOT_STOP_MAX_CONTINUES）に達した → systemMessage で人に出す
# 続けさせる（{"decision":"block","reason":...}）:
#   - 上記のどれでもなく、dod に緑でない項目がある／dod は全部緑だが 5.5 mutate・6 deliver が残っている
#
# 続行回数は oneshot/.hooks.json（hook 専用）に持つ。state.json は周の書き手のもので、hook が書き換えると
# 書き手の Read と食い違う（pre-write-collision が止める）ので分ける。
# 仕様: https://code.claude.com/docs/en/hooks （Stop input / Stop decision control・2026-09-25 原文で確認）
# =============================================================
set -u
IN=$(cat 2>/dev/null) || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
PROG=''
read -r -d '' PROG <<'PY' || true
import hashlib, json, os, sys, time, datetime

TERMINAL = ("delivered", "escalated", "stopped")

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
side_path = os.path.join(odir, ".hooks.json")
side = load(side_path)
if not isinstance(side, dict):
    side = {}
try:
    cap = max(1, int(os.environ.get("ONESHOT_STOP_MAX_CONTINUES", "3")))
except ValueError:
    cap = 3

def save_side():
    try:
        tmp = side_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(side, fh, ensure_ascii=False)
        os.replace(tmp, side_path)
        return True
    except Exception:
        return False

def reset_and_allow():
    if side.get("stop"):
        side.pop("stop", None)
        save_side()
    sys.exit(0)

def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)

# 起こされ待ち（完了ではない）。続けさせず、回数も使わない
if d.get("background_tasks") or d.get("session_crons"):
    sys.exit(0)
# 人の割り込み・終わった状態・人が要る理由がある → 止まってよい
if os.path.exists(os.path.join(odir, "STOP")):
    reset_and_allow()
status = str(st.get("status") or "")
if status in TERMINAL:
    reset_and_allow()
if str(st.get("blocker") or "").strip():
    reset_and_allow()

dod = st.get("dod")
if not isinstance(dod, list) or not dod:
    emit({"systemMessage": "❓ oneshot: state.json に dod が無い — 未完了かどうか判定できないので止めた（続行させていない）"})

open_items = []
for i, e in enumerate(dod):
    if not isinstance(e, dict):
        continue
    res = str(e.get("result") or "未実行")
    if res != "green":
        open_items.append("dod[%d] %s `%s` → %s" % (i, e.get("kind") or "?", e.get("cmd") or "?", res))
if not open_items:
    open_items.append("dod は %d 本とも green。残りは §3-5.5 mutate → §3-6 deliver（PR を開いて status を delivered にする）" % len(dod))

# 経過時間（あれば理由に添える・助言。hard stop は goal-loop の外側の CAP minutes）
elapsed = ""
sa = st.get("started_at")
try:
    if isinstance(sa, (int, float)):
        t0 = float(sa)
    else:
        t0 = datetime.datetime.fromisoformat(str(sa).replace("Z", "+00:00")).timestamp()
    sec = int(time.time() - t0)
    mins = (st.get("budget") or {}).get("minutes")
    elapsed = "elapsed %ds / %ds" % (sec, int(float(mins) * 60)) if mins else "elapsed %ds" % sec
except Exception:
    pass

key = hashlib.sha1("\n".join(open_items).encode("utf-8")).hexdigest()[:12]
prev = side.get("stop") or {}
count = int(prev.get("count", 0)) + 1 if prev.get("key") == key else 1
if count > cap:
    side["stop"] = {"key": key, "count": count, "capped": True}
    save_side()
    emit({"systemMessage": "oneshot: 同じ未完了項目のまま自動続行を %d 回した — 止めた（人が見る）。未完了: %s" % (cap, " / ".join(open_items))})
side["stop"] = {"key": key, "count": count}
if not save_side() and d.get("stop_hook_active"):
    sys.exit(0)  # 回数を記録できない・既に続行中 → 数えられないまま続けさせない
reason = (
    "oneshot は終わっていない。完了は dod の exit code だけで判定する（テキストで手番を終えても完了ではない）。"
    "未完了: %s。続けてください。進捗の報告は次の操作と同じメッセージに書く。"
    "人の判断が要る・進められないなら、§3-7 escalate（blocker を 1 行記録・status=escalated）で止まる。"
    "（自動続行 %d/%d 回目%s）" % (" / ".join(open_items), count, cap, "・" + elapsed if elapsed else "")
)
emit({"decision": "block", "reason": reason})
PY
printf '%s' "$IN" | python3 -c "$PROG" 2>/dev/null
exit 0
