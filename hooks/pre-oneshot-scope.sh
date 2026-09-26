#!/usr/bin/env bash
# =============================================================
# pre-oneshot-scope.sh — /oneshot の無人区間で、契約の外へ手を出させない（PreToolUse）
#
# なぜ（2026-09-25・docs/design/oneshot-mode.md §4）:
#   無人で回る部分の品質は契約（oneshot.yaml）の品質と同じにしかならない。契約で決めた
#   `scope`（触ってよい範囲）の外を書き換えたり、契約と gate の設定自身（oneshot.yaml・
#   oneshot/・.claude/・forbid.gate_config）を緩めて通したり、外へ到達したり（merge・
#   deploy・公開・POST）できる無人プロセスは作らない。
#
# 何もしない（exit 0・出力なし）:
#   - oneshot/state.json が無い（/oneshot を使っていないリポ・Phase −1 の対話中すべて）
#   - status が running でない（delivered / escalated / stopped）
# 止める（exit 2・理由と代わりの手順を stderr に出す）— status=running のとき:
#   - Write / Edit / MultiEdit / NotebookEdit の対象が scope の外・forbid・gate_config・リポの外
#     （oneshot/plan.md だけは計画の置き場として書いてよい）
#   - Bash が外へ到達する: PR の merge・release・deploy・publish・push の force / 既定ブランチ宛て・
#     書き込み系の HTTP（curl -X POST / --data など）・クラウドの変更系コマンド
#   - 契約が読めない（fail-closed。無人区間で「測れない」を「通す」にしない）
#   - status に関係なく: ツール経由の `oneshot-preflight.py resume`（resume は人だけ。人は自分の端末で呼ぶ）
# 止まっている間（escalated / stopped）: 契約・spec・gate 設定・oneshot/ への Claude の書き込み（再開の基準を守る）
# 守る対象のリポは書き込み先のパス（Write）と、cwd・コマンド内の `cd <path>` / `-C <path>`（Bash）で決める。
# 契約の読み方は scripts/_oneshot.py と同じ（preflight と食い違わないよう共通部品を使う）。
# =============================================================
set -u
IN=$(cat 2>/dev/null) || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
HERE=$(cd "$(dirname "$0")" && pwd)
LIB=""
for c in "$HERE/../scripts/_oneshot.py" "$HERE/../../scripts/_oneshot.py" "$HERE/../engines/_oneshot.py"; do
  [ -f "$c" ] && { LIB="$c"; break; }
done
PROG=''
read -r -d '' PROG <<'PY' || true
import json, os, re, sys
lib = sys.argv[1]
try:
    d = json.loads(sys.stdin.read() or "{}")
except Exception:
    sys.exit(0)

import subprocess

def toplevel(path):
    """path（ファイルでもディレクトリでも・まだ無くても）が属する git リポのルート。無ければ ""。"""
    d = path if os.path.isdir(path) else os.path.dirname(path)
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    if not d:
        return ""
    try:
        out = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""

def load_st(root):
    try:
        with open(os.path.join(root, "oneshot", "state.json"), encoding="utf-8") as fh:
            v = json.load(fh)
        return v if isinstance(v, dict) else None
    except Exception:
        return None

def O_relpath_outside(root, path):
    a, r = os.path.realpath(path), os.path.realpath(root)
    return not (a == r or a.startswith(r + os.sep))

def block(why, alt):
    sys.stderr.write("BLOCKED (oneshot): %s\nAlternative: %s\n" % (why, alt))
    sys.exit(2)

ESCALATE = ("人の判断が要るなら `python3 <kit>/scripts/oneshot-preflight.py note --blocker \"<理由 1 行>\" --status escalated` で止まる"
            "（§3-7）。人は oneshot/STOP を置いて /build（対話）へ移れる")
tool = d.get("tool_name") or ""
ti = d.get("tool_input") or {}
bases = [b for b in (d.get("cwd") or "", os.environ.get("CLAUDE_PROJECT_DIR", "")) if b]

# 守る対象のリポは「どこで動いているか」ではなく「どこに書くか・どこを動かし得るか」で決める。
# cwd が主 checkout のままでも、絶対パスで worktree に書く Write や `cd <wt> && …` の Bash を見逃さない
if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
    p = ti.get("file_path") or ti.get("notebook_path") or ""
    if not p:
        sys.exit(0)
    ap = p if os.path.isabs(p) else os.path.join(bases[0] if bases else os.getcwd(), p)
    r = toplevel(ap)
    roots = [os.path.realpath(r)] if r else []
    if not (roots and load_st(roots[0])):
        # 書き込み先のリポに state が無い。このセッションが無人区間（cwd のリポが running）なら、リポの外への
        # 書き込みとして止める。別のリポ・別のセッションの普段の作業は止めない
        for b in bases:
            t = toplevel(b)
            st0 = load_st(t) if t else None
            if st0 and st0.get("status") == "running" and O_relpath_outside(t, ap):
                block("`%s` は無人区間のリポ（%s）の外 — 無人区間はリポの中の scope だけを書く" % (p, t), ESCALATE)
        sys.exit(0)
elif tool == "Bash":
    cmd0 = str(ti.get("command") or "")
    # cwd のリポに加え、コマンドの中で移る先（`cd <path>` / `git -C <path>`）のリポも見る。worktree を全部は見ない
    # （同じリポの主 checkout で人が別セッションから打つ `gh pr merge` まで止めてしまうので）
    targets = list(bases)
    for m in re.finditer(r"(?:\bcd|\bpushd|\s-C|\s--root)(?:\s+|=)(['\"]?)([^\s;&|'\"]+)\1", cmd0):
        q = os.path.expanduser(m.group(2))
        targets.append(q if os.path.isabs(q) else os.path.join(bases[0] if bases else os.getcwd(), q))
    roots = []
    for b in targets:
        t = toplevel(b)
        if t:
            roots.append(os.path.realpath(t))
    roots = list(dict.fromkeys(roots))
else:
    sys.exit(0)
states = {r: st for r in roots for st in [load_st(r)] if st is not None}
if not states:
    sys.exit(0)

if tool == "Bash":
    cmd = str(ti.get("command") or "")
    # resume は「人がループに戻って直した」印。Claude が自分で escalate → 契約や scope 外を書き換え → resume、と
    # 無人区間の柵を外せないよう、ツール経由の resume は status に関係なく止める（人は自分の端末で実行する）。
    # 引用符・バックスラッシュで語を割っても外れないよう、落としてから照合する
    flat = re.sub(r"[\"'\\]", "", cmd)
    SUB = r"oneshot-preflight\.py(?:\s+--root(?:\s+|=)\S+)*\s+%s\b"   # サブコマンドとして呼ばれたときだけ（文中の語では止めない）
    if re.search(SUB % "start", flat):
        unfinished = [(r, st.get("status")) for r, st in states.items() if st.get("status") != "delivered"]
        if unfinished:
            block("前回の oneshot が終わっていない（status=%s）— start し直すと周の数・予算・基準点が初期化される" % unfinished[0][1],
                  "再開は人が自分の端末で `oneshot-preflight.py resume`。やめるなら人が oneshot/state.json を片付けてから")
    if re.search(SUB % "resume", flat):
        block("oneshot の resume は人だけが呼ぶ（止まっている間に人が直したものを承認して再開する口）",
              "人に頼む: 人が自分の端末（Claude Code の外のシェル）で `python3 <kit>/scripts/oneshot-preflight.py resume [--accept <path>]` を実行する")
    running = [r for r, st in states.items() if st.get("status") == "running"]
    if not running:
        sys.exit(0)
    root = running[0]
else:
    root = roots[0]
    st = states.get(root)
    if st is None:
        sys.exit(0)
    status = st.get("status")
    if status in ("escalated", "stopped"):
        # 止まっている間は、再開の基準になるもの（契約・spec・gate 設定・oneshot/ の state）を Claude が書かない。
        # 直すのは人（人が直して spec を作り直し、commit して resume）
        if not lib:
            block("共通部品 _oneshot.py が見つからない — 止まっている間の契約を守れない（fail-closed）", ESCALATE)
        sys.path.insert(0, os.path.dirname(lib))
        import _oneshot as O  # noqa: E402
        c, _err = O.load_contract(root)
        rel = O.relpath(root, ap)
        prot = [O.CONTRACT] + (O.gate_config(c) if c else [])
        if rel is not None and rel not in O.WRITABLE_IN_ONESHOT and (
                rel in prot or rel.startswith("oneshot/") or any(O.path_match(rel, g) for g in prot)):
            block("`%s` は oneshot が止まっている間（status=%s）の再開の基準（契約・spec・gate 設定・state）— Claude は書かない" % (rel, status),
                  "直すのは人: 人が契約を直し `oneshot-spec.py spec` で spec を作り直して commit → 自分の端末で `oneshot-preflight.py resume`")
        sys.exit(0)
    if status != "running":
        sys.exit(0)

# ここから status=running（無人区間）。契約を読めなければ通さない（fail-closed）
if not lib:
    block("共通部品 _oneshot.py が見つからない — 契約を読めないので無人区間では通さない（fail-closed）",
          "kit を入れ直す（claude plugin update willink-claude-kit@iwillink）か、" + ESCALATE)
sys.path.insert(0, os.path.dirname(lib))
import _oneshot as O  # noqa: E402
c, err = O.load_contract(root)
if c is None:
    block("契約が読めない（%s）— 無人区間では通さない（fail-closed）" % err, ESCALATE)

if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
    rel = O.relpath(root, ap)
    if rel is None:
        block("`%s` はリポの外 — 無人区間はリポの中の scope だけを書く" % p, ESCALATE)
    kind = O.classify_path(c, rel)
    if kind == "writable":
        sys.exit(0)
    scope = ", ".join(O.scope_paths(c)) or "（空）"
    if kind == "gate":
        block("`%s` は gate の設定（forbid.gate_config）— 緩めて通す逃げ道になるので無人では触らない" % rel,
              "実装側を直して gate を通す。gate の設定を変える必要があるなら " + ESCALATE)
    if kind == "forbid":
        block("`%s` は forbid（契約・oneshot/・.claude/・forbid.paths）— scope に勝つ" % rel,
              "state の更新は `oneshot-preflight.py note` で行う（Write で oneshot/ を書かない）。計画は oneshot/plan.md に書いてよい。それ以外は " + ESCALATE)
    block("`%s` は scope の外（scope: %s）" % (rel, scope),
          "scope の中で実装を済ませる。scope を広げるのは人（oneshot/STOP → oneshot.yaml の scope に足す → start し直す）。" + ESCALATE)

if tool == "Bash":
    cmd = str(ti.get("command") or "")
    # 引用符の中身ごと見る（`bash -c "firebase deploy"` も止める）。語の境界は空白・; & | ( ` と引用符で取る
    B = r"(?:^|[\s;&|(`'\"])"
    rules = [
        (B + r"gh\s+pr\s+merge\b", "PR の merge（oneshot は PR を開いて止まる・merge は人）"),
        (B + r"gh\s+release\b", "release の作成・変更"),
        (B + r"gh\s+repo\s+(?:delete|edit|rename|archive)\b", "リポの設定変更"),
        (B + r"gh\s+api\b[^\n]*(?:-X|--method)[\s=]*(?:POST|PUT|PATCH|DELETE)\b", "gh api の書き込み"),
        (B + r"gh\s+api\s+graphql\b[^\n]*\bmutation\b", "gh api の書き込み（GraphQL mutation）"),
        (B + r"curl\b[^\n]*(?:-X\s*(?:POST|PUT|PATCH|DELETE)|--request[\s=]+(?:POST|PUT|PATCH|DELETE)|\s-d|\s--data|\s--json\b|\s-F|--form|--upload-file|\s-T\s)", "書き込み系の HTTP"),
        (B + r"wget\b[^\n]*--post", "書き込み系の HTTP"),
        (B + r"git(?:\s+-[Cc]\s+\S+)*\s+push\b[^\n]*(?:\s--force\b|\s-f\b|--force-with-lease|--mirror|--delete|\s-d\b|\s:\S|\s\+\S)", "force push・ブランチ削除"),
        (B + r"git(?:\s+-[Cc]\s+\S+)*\s+push\b[^\n]*[\s:](?:refs/heads/)?(?:main|master)(?=$|[\s;&|)])", "既定ブランチへの push（oneshot は PR 経由）"),
        (B + r"(?:firebase|netlify|fly|flyctl|railway|wrangler)\s+deploy\b", "deploy"),
        (B + r"vercel\b[^\n]*(?:--prod|\sdeploy\b)", "deploy"),
        (B + r"gcloud\b[^\n]*\s(?:deploy|delete|create|update)\b", "クラウドの変更"),
        (B + r"aws\s+\S+[^\n]*\s(?:deploy|delete-\S+|put-\S+|create-\S+|update-\S+|terminate-\S+|rm|cp|mv|sync)\b", "クラウドの変更"),
        (B + r"(?:kubectl\s+(?:apply|delete|create|patch|rollout|scale)|terraform\s+(?:apply|destroy)|pulumi\s+up|helm\s+(?:install|upgrade|uninstall))\b", "インフラの変更"),
        (B + r"(?:npm|pnpm|yarn)\s+publish\b|twine\s+upload\b|cargo\s+publish\b|gem\s+push\b|docker\s+push\b|pod\s+trunk\s+push\b", "公開（publish）"),
        (B + r"(?:fastlane\b|xcrun\s+altool\b|xcrun\s+notarytool\b)", "ストアへの提出"),
    ]
    for rx, what in rules:
        if re.search(rx, cmd):
            block("無人区間では外へ到達しない: %s — `%s`" % (what, cmd[:160]),
                  "oneshot は L1（リポの中の変更と PR を開くまで）。push はブランチへ・PR は `gh pr create --draft` で開ける。それ以外は " + ESCALATE)
sys.exit(0)
PY
printf '%s' "$IN" | python3 -c "$PROG" "$LIB"
