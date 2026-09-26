"""/oneshot の共通部品（契約の読み込み・検査・パスの照合・state.json の読み書き・実行件数の抽出）。

なぜ 1 つにまとめるか（2026-09-25・docs/design/oneshot-mode.md §8）:
  preflight（契約の検査・毎周の判定・mutation）・spec（Phase −1 の機械側）・report（報告書）・
  scope hook（scope 外への Write を止める）が **同じ契約を同じ意味で読む** 必要がある。
  読み方が 1 か所でもずれると「preflight は通るが hook は止める」のような食い違いが起き、
  無人区間の途中で理由の分からない停止になる。

契約（oneshot.yaml）の読み方:
  pyyaml があればそれで読む。無ければ下の最小パーサ（コメント・ブロックの map / list・
  flow の [..] / {..}・引用符つき文字列・整数・空値）で読む。self-test は pyyaml がある環境で
  **両者が同じ結果になること** を確かめる（パーサが 2 つあることの危険を self-test で塞ぐ）。

state.json（oneshot/state.json・リポには commit しない）の項目は §3.1 と同じ。hook
（stop-oneshot-continue.sh / post-oneshot-elapsed.sh / pre-oneshot-scope.sh）はこの項目を読む。
**state.json は無人区間に入る時点（preflight start）で初めて作る。** Phase −1（対話）の間に在ると、
Stop hook が利用者への質問を「未完了のまま止まった」と扱って続けさせてしまう。

python3 _oneshot.py --self-test
"""

from __future__ import annotations

import datetime
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys

KINDS = ("new", "regression", "boundary")
TERMINAL = ("delivered", "escalated", "stopped")
# 契約と gate の設定自身は、書かなくても forbid に入る（§2.1 問 3・§4）
DEFAULT_FORBID = ("oneshot.yaml", "oneshot/", ".claude/")
# 無人区間でも Write / Edit で書いてよい oneshot/ 配下（計画は人があとで読む成果物）
WRITABLE_IN_ONESHOT = ("oneshot/plan.md",)
CONTRACT = "oneshot.yaml"
ODIR = "oneshot"


# ─────────────────────────────── YAML（最小パーサ）───────────────────────────────

class YamlError(ValueError):
    pass


def _strip_comment(line: str) -> str:
    """引用符の外にある `#`（直前が空白か行頭）から後ろを落とす。
    引用符が文字列の始まりになるのは、値の先頭（行頭・`:` `-` `[` `{` `,` の後）に来たときだけ。
    `cmd: grep -q "a #b" f` のように引用符の無い値の途中の `"` は文字で、その後ろの ` #` はコメント（pyyaml と同じ）。"""
    out, q, i = [], "", 0
    while i < len(line):
        c = line[i]
        if q:
            out.append(c)
            if q == '"' and c == "\\" and i + 1 < len(line):
                out.append(line[i + 1]); i += 2; continue
            if c == q:
                if q == "'" and i + 1 < len(line) and line[i + 1] == "'":
                    out.append("'"); i += 2; continue
                q = ""
        else:
            prev = "".join(out).rstrip()
            if c in "\"'" and (not prev or prev[-1] in ":[{,-"):
                q = c
            elif c == "#" and (not out or out[-1] in " \t"):
                break
            out.append(c)
        i += 1
    return "".join(out).rstrip()


_DQ_ESC = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "t": "\t", "r": "\r", "0": "\0", " ": " "}


def _scalar(tok: str):
    t = tok.strip()
    if t == "" or t in ("~", "null", "Null", "NULL"):
        return None
    if t[0] == '"':
        if len(t) < 2 or t[-1] != '"':
            raise YamlError("閉じていない二重引用符: %s" % t)
        body, out, i = t[1:-1], [], 0
        while i < len(body):
            c = body[i]
            if c == "\\":
                if i + 1 >= len(body) or body[i + 1] not in _DQ_ESC:
                    raise YamlError("二重引用符の中の不正なエスケープ: %s" % t)
                out.append(_DQ_ESC[body[i + 1]]); i += 2; continue
            out.append(c); i += 1
        return "".join(out)
    if t[0] == "'":
        if len(t) < 2 or t[-1] != "'":
            raise YamlError("閉じていない一重引用符: %s" % t)
        return t[1:-1].replace("''", "'")
    if t in ("true", "True", "TRUE"):
        return True
    if t in ("false", "False", "FALSE"):
        return False
    if re.fullmatch(r"[-+]?\d+", t):
        return int(t)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+)([eE][-+]?\d+)?", t):
        return float(t)
    return t


def _split_flow(body: str):
    """flow の中身を最上位の `,` で分ける（引用符・入れ子の括弧を考慮）。"""
    parts, depth, q, cur, i = [], 0, "", [], 0
    while i < len(body):
        c = body[i]
        if q:
            cur.append(c)
            if q == '"' and c == "\\" and i + 1 < len(body):
                cur.append(body[i + 1]); i += 2; continue
            if c == q:
                q = ""
        elif c in "\"'":
            q = c; cur.append(c)
        elif c in "[{":
            depth += 1; cur.append(c)
        elif c in "]}":
            depth -= 1; cur.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(cur)); cur = []
        else:
            cur.append(c)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur))
    return [p.strip() for p in parts]


def _split_key(s: str):
    """`key: value` の `: ` で分ける（引用符の中の `:` は無視）。見つからなければ None。"""
    q = ""
    for i, c in enumerate(s):
        if q:
            if c == q:
                q = ""
            continue
        if c in "\"'":
            q = c
        elif c == ":" and (i + 1 == len(s) or s[i + 1] in " \t"):
            return s[:i].strip(), s[i + 1:].strip()
    return None


def _value(tok: str):
    t = tok.strip()
    if t.startswith("["):
        if not t.endswith("]"):
            raise YamlError("閉じていない [: %s" % t)
        return [_value(p) for p in _split_flow(t[1:-1])]
    if t.startswith("{"):
        if not t.endswith("}"):
            raise YamlError("閉じていない {: %s" % t)
        out = {}
        for p in _split_flow(t[1:-1]):
            kv = _split_key(p)
            if kv is None:
                raise YamlError("flow map の要素に key が無い: %s" % p)
            out[_key(kv[0])] = _value(kv[1])
        return out
    return _scalar(t)


def _key(k: str):
    v = _scalar(k)
    return v if isinstance(v, str) else str(k).strip()


def parse_yaml_subset(text: str):
    lines = []
    for raw in text.splitlines():
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlError("インデントに tab がある")
        s = _strip_comment(raw)
        if s.strip():
            lines.append((len(s) - len(s.lstrip(" ")), s.strip()))
    if not lines:
        return None
    pos = [0]

    def block(ind):
        if pos[0] >= len(lines):
            return None
        _, first = lines[pos[0]]
        if first == "-" or first.startswith("- "):
            return seq(ind)
        return mapping(ind)

    def seq(ind):
        out = []
        while pos[0] < len(lines):
            i, s = lines[pos[0]]
            if i < ind or not (s == "-" or s.startswith("- ")):
                break
            if i > ind:
                raise YamlError("list のインデントが揃っていない: %s" % s)
            rest = s[1:].strip()
            rest_ind = ind + len(s) - len(s[1:].lstrip(" "))   # `-` の後ろの空白ぶん（`-   kind:` にも合わせる）
            pos[0] += 1
            if not rest:
                out.append(block(_child_indent(ind)))
            elif _split_key(rest) and not rest.startswith(("[", "{", '"', "'")):
                # `- key: v` と、その下に続く同じ map の key
                lines.insert(pos[0], (rest_ind, rest))
                out.append(mapping(rest_ind))
            else:
                out.append(_value(rest))
        return out

    def _child_indent(ind):
        return lines[pos[0]][0] if pos[0] < len(lines) and lines[pos[0]][0] > ind else ind + 1

    def mapping(ind):
        out = {}
        while pos[0] < len(lines):
            i, s = lines[pos[0]]
            if i < ind:
                break
            if i > ind:
                raise YamlError("map のインデントが揃っていない: %s" % s)
            kv = _split_key(s)
            if kv is None:
                raise YamlError("key: value の形でない行: %s" % s)
            k, v = kv
            pos[0] += 1
            if v == "":
                nxt = lines[pos[0]] if pos[0] < len(lines) else None
                if nxt and (nxt[0] > ind or (nxt[0] == ind and (nxt[1] == "-" or nxt[1].startswith("- ")))):
                    out[_key(k)] = block(nxt[0])
                else:
                    out[_key(k)] = None
            else:
                out[_key(k)] = _value(v)
        return out

    res = block(lines[0][0])
    if pos[0] != len(lines):
        raise YamlError("読み残しがある行: %s" % lines[pos[0]][1])
    return res


def load_yaml(text: str):
    """pyyaml があればそれで、無ければ最小パーサで読む。読めなければ YamlError。"""
    try:
        import yaml  # type: ignore
    except ImportError:
        return parse_yaml_subset(text)
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:  # type: ignore[attr-defined]
        raise YamlError(str(e).splitlines()[0]) from None


# ─────────────────────────────── 契約 ───────────────────────────────

def repo_root(start: str | None = None) -> str:
    env = os.environ.get("PH_TARGET_ROOT")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start or os.getcwd(),
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return os.path.abspath(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.abspath(start or os.getcwd())


def load_contract(root: str, path: str | None = None):
    """(contract dict, error str)。読めない・map でない → (None, 理由)。"""
    p = path or os.path.join(root, CONTRACT)
    try:
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        return None, "契約が読めない: %s（%s）" % (p, e.strerror or e)
    try:
        c = load_yaml(text)
    except YamlError as e:
        return None, "契約の YAML が読めない: %s" % e
    if not isinstance(c, dict):
        return None, "契約の最上位が map でない"
    return c, ""


def _paths(v):
    if isinstance(v, dict):
        v = v.get("paths")
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v if x not in (None, "")]


def scope_paths(c) -> list:
    return _paths(c.get("scope"))


def forbid_paths(c) -> list:
    own = _paths(c.get("forbid"))
    return list(dict.fromkeys(list(DEFAULT_FORBID) + own))


def gate_config(c) -> list:
    f = c.get("forbid")
    g = f.get("gate_config") if isinstance(f, dict) else None
    return _paths(g)


def _pos_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def validate_contract(c) -> list:
    """問題の list。各要素 {"code","level","msg"}。level = phase-1（対話へ戻す）| reject（exit 2）。"""
    probs = []

    def p(code, level, msg):
        probs.append({"code": code, "level": level, "msg": msg})

    if not isinstance(c.get("goal"), str) or not c.get("goal", "").strip():
        p("goal", "phase-1", "goal（1 文）が無い")
    dod = c.get("dod")
    if not isinstance(dod, list) or not dod:
        p("dod-empty", "phase-1", "dod が空 — 完了を判定できない")
        dod = []
    kinds = set()
    for i, e in enumerate(dod):
        if not isinstance(e, dict):
            p("dod-shape", "phase-1", "dod[%d] が map でない" % i); continue
        k = e.get("kind")
        if k not in KINDS:
            p("dod-kind", "phase-1", "dod[%d] の kind が %s のどれでもない: %r" % (i, "/".join(KINDS), k))
        else:
            kinds.add(k)
        if not isinstance(e.get("cmd"), str) or not e.get("cmd", "").strip():
            p("dod-cmd", "phase-1", "dod[%d] に cmd が無い" % i)
        if k == "new":
            if not isinstance(e.get("mutate"), str) or not e.get("mutate", "").strip():
                p("mutate", "phase-1", "dod[%d]（new）に mutate が無い — 壊せば赤になる 1 手が要る（mutation-first は必須）" % i)
            cnt = e.get("count")
            if not isinstance(cnt, str) or not cnt.strip():
                p("count", "phase-1", "dod[%d]（new）に count が無い — 実行件数の取り方が要る（0 件でも exit 0 の runner がある）" % i)
            else:
                try:
                    re.compile(cnt)
                except re.error as ex:
                    p("count", "phase-1", "dod[%d] の count が正規表現として読めない: %s" % (i, ex))
    for k in KINDS:
        if dod and k not in kinds:
            p("dod-kinds", "phase-1", "dod に kind=%s が無い — 新規 / 回帰 / 境界の 3 種すべてが要る" % k)
    b = c.get("budget")
    b = b if isinstance(b, dict) else {}
    for k in ("attempts", "minutes", "tokens"):
        if not _pos_int(b.get(k)):
            p("budget", "phase-1", "budget.%s が空か正の整数でない — 既定値は置かない（Phase −1 の問 6 から導いて利用者が確定する）" % k)
    lv = c.get("level")
    if not isinstance(lv, int) or isinstance(lv, bool):
        p("level", "phase-1", "level が無い（L1 のみ）")
    elif lv >= 2:
        p("level", "reject", "level=%d — 顧客リポ・公開・価格は都度承認。無人の oneshot には入れない" % lv)
    if not scope_paths(c):
        p("scope", "reject", "scope.paths が空 — どこでも触れる無人プロセスは作らない")
    return probs


# ─────────────────────────────── パスの照合 ───────────────────────────────

def _norm(rel: str) -> str:
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def path_match(rel: str, pattern: str) -> bool:
    """`dir/` は前方一致・glob 文字があれば fnmatch（パス全体と basename）・それ以外は完全一致か配下。"""
    rel, pat = _norm(rel), _norm(pattern)
    if not pat:
        return False
    if pat.endswith("/"):
        return rel == pat[:-1] or rel.startswith(pat)
    if any(ch in pat for ch in "*?["):
        return fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(os.path.basename(rel), pat)
    return rel == pat or rel.startswith(pat + "/")


def relpath(root: str, path: str):
    """root 配下の相対パス（root の外なら None）。"""
    ap = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    rr = os.path.realpath(root)
    if ap != rr and not ap.startswith(rr + os.sep):
        return None
    return _norm(os.path.relpath(ap, rr))


def classify_path(c, rel: str) -> str:
    """"writable" | "forbid" | "gate" | "out-of-scope"。forbid は scope に勝つ。"""
    if rel in WRITABLE_IN_ONESHOT:
        return "writable"
    if any(path_match(rel, g) for g in gate_config(c)):
        return "gate"
    if any(path_match(rel, f) for f in forbid_paths(c)):
        return "forbid"
    if any(path_match(rel, s) for s in scope_paths(c)):
        return "writable"
    return "out-of-scope"


# ─────────────────────────────── 実行件数 ───────────────────────────────

def count_from_output(pattern: str, text: str):
    """group があれば全一致の group(1) の和、無ければ一致の個数。1 つも一致しなければ None（測れていない）。"""
    try:
        rx = re.compile(pattern)
    except re.error:
        return None
    ms = list(rx.finditer(text))
    if not ms:
        return None
    if rx.groups:
        total = 0
        for m in ms:
            try:
                total += int(m.group(1))
            except (TypeError, ValueError):
                return None
        return total
    return len(ms)


# ─────────────────────────────── state.json ───────────────────────────────

def state_path(root: str) -> str:
    return os.path.join(root, ODIR, "state.json")


def load_state(root: str):
    try:
        with open(state_path(root), encoding="utf-8") as fh:
            st = json.load(fh)
        return st if isinstance(st, dict) else None
    except (OSError, ValueError):
        return None


def save_state(root: str, st: dict) -> None:
    os.makedirs(os.path.join(root, ODIR), exist_ok=True)
    p = state_path(root)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, p)


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s) -> float | None:
    try:
        if isinstance(s, (int, float)):
            return float(s)
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def sha256_file(path: str):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# ─────────────────────────────── self-test ───────────────────────────────

_SAMPLE = r'''# 契約のサンプル（ryoribon の型を匿名化）
goal: "共有シートから受けたメモを構造化して保存する"   # 1 文
dod:
  - kind: new                                          # red-first の対象
    cmd: "make test"
    count: 'Executed (\d+) tests?'
    mutate: "sed -i '' 's/if terms.isEmpty { return false }/if terms.isEmpty { return true }/' App/Normalizer.swift"
  - kind: regression
    cmd: "make functions && make rules"
  - kind: boundary
    cmd: "bash scripts/harness-check.sh && diff -q a.json b.json"
scope:
  paths: ["App/", "Tests/", "project.yml"]
forbid:
  paths: ["infra/", ".github/", "*.env*", "oneshot.yaml"]
  gate_config: [".claude/harness-targets.json"]
budget:
  attempts: 3
  minutes: 240
  tokens: 400000
level: 1
'''

_SAMPLE_FLOW = r'''goal: "x # これは値の中"
dod:
  - {kind: new, cmd: "npm test", count: 'Tests:\s+(\d+) passed', mutate: "sed -i 's/a/b/' src/a.ts"}
  - kind: regression
    cmd: npm run typecheck
  -
    kind: boundary
    cmd: 'it''s ok'
scope: { paths: ["src/"] }
forbid: { paths: [], gate_config: ["floor.json"] }
budget: { attempts: 0, minutes: , tokens: 10 }
level: 1
empty:
'''


def _self_test() -> int:
    ok = ng = 0

    def check(name, cond, detail=""):
        nonlocal ok, ng
        if cond:
            ok += 1; print("  ✅ " + name)
        else:
            ng += 1; print("  ❌ " + name + (("  — " + detail) if detail else ""))

    print("── _oneshot self-test ──")
    c = parse_yaml_subset(_SAMPLE)
    check("最小パーサ: 契約を map で読む", isinstance(c, dict) and c.get("level") == 1, repr(c)[:200])
    check("最小パーサ: dod 3 本・kind の並び", [e.get("kind") for e in c["dod"]] == ["new", "regression", "boundary"])
    check("最小パーサ: 値の中の # はコメントでない（引用符の中）", c["dod"][2]["cmd"].endswith("diff -q a.json b.json"))
    check("最小パーサ: 一重引用符の正規表現はそのまま", c["dod"][0]["count"] == r"Executed (\d+) tests?")
    check("最小パーサ: 行末コメントを落とす", c["goal"] == "共有シートから受けたメモを構造化して保存する")
    check("最小パーサ: flow list", c["scope"]["paths"] == ["App/", "Tests/", "project.yml"])
    f = parse_yaml_subset(_SAMPLE_FLOW)
    check("最小パーサ: flow map の dod 要素", f["dod"][0]["kind"] == "new" and f["dod"][0]["cmd"] == "npm test", repr(f.get("dod"))[:200])
    check("最小パーサ: `-` 単独行の下の map", f["dod"][2] == {"kind": "boundary", "cmd": "it's ok"}, repr(f["dod"][2]))
    check("最小パーサ: 引用符の中の # は残る", f["goal"] == "x # これは値の中")
    check("最小パーサ: flow map の空値は None", f["budget"] == {"attempts": 0, "minutes": None, "tokens": 10}, repr(f["budget"]))
    check("最小パーサ: 値の無い key は None", f["empty"] is None)
    w = parse_yaml_subset("dod:\n  -   kind: new\n      cmd: x\n  - kind: boundary\n    cmd: y\n")
    check("最小パーサ: `-   kind:` のように空白が多くても同じ map", w == {"dod": [{"kind": "new", "cmd": "x"}, {"kind": "boundary", "cmd": "y"}]}, repr(w))
    try:
        parse_yaml_subset('goal: "閉じていない\n')
        check("最小パーサ: 閉じていない引用符は YamlError", False)
    except YamlError:
        check("最小パーサ: 閉じていない引用符は YamlError", True)
    try:
        import yaml  # type: ignore
        odd = 'dod:\n  - kind: boundary\n    cmd: grep -q "a #b" f\n  - kind: new\n    cmd: x  # c\n    mutate: "sed -i s/a/b/ f"\n'
        for name, text in (("契約サンプル", _SAMPLE), ("flow サンプル", _SAMPLE_FLOW.replace("minutes: ,", "minutes: null,")),
                           ("引用符の無い値の途中の \" と #", odd)):
            a, b = parse_yaml_subset(text), yaml.safe_load(text)
            check("pyyaml と最小パーサが同じ結果（%s）" % name, a == b, "subset=%r\npyyaml=%r" % (a, b))
    except ImportError:
        print("  ℹ️  pyyaml が無い — 最小パーサとの突き合わせは skip（最小パーサ単体の検査は上で済）")

    probs = validate_contract(c)
    check("正しい契約は問題 0", probs == [], repr(probs))
    bad = dict(c); bad["dod"] = [e for e in c["dod"] if e["kind"] != "boundary"]
    check("境界が無い → dod-kinds（phase-1）", any(x["code"] == "dod-kinds" and x["level"] == "phase-1" for x in validate_contract(bad)))
    bad = json.loads(json.dumps(c)); bad["dod"][0].pop("mutate")
    check("new に mutate が無い → mutate", any(x["code"] == "mutate" for x in validate_contract(bad)))
    bad = json.loads(json.dumps(c)); bad["dod"][0].pop("count")
    check("new に count が無い → count", any(x["code"] == "count" for x in validate_contract(bad)))
    bad = json.loads(json.dumps(c)); bad["budget"]["minutes"] = None
    check("budget の空 → budget（既定値を置かない）", any(x["code"] == "budget" for x in validate_contract(bad)))
    bad = json.loads(json.dumps(c)); bad["level"] = 2
    check("level 2 → reject", any(x["code"] == "level" and x["level"] == "reject" for x in validate_contract(bad)))
    bad = json.loads(json.dumps(c)); bad["scope"] = {"paths": []}
    check("scope 空 → reject", any(x["code"] == "scope" and x["level"] == "reject" for x in validate_contract(bad)))
    check("flow サンプルは budget 0 / 空で phase-1", sum(1 for x in validate_contract(f) if x["code"] == "budget") == 2)

    check("forbid の既定（oneshot.yaml / oneshot/ / .claude/）が常に入る", all(d in forbid_paths(f) for d in DEFAULT_FORBID))
    check("path: dir/ は配下に一致", path_match("App/Views/A.swift", "App/"))
    check("path: dir/ は名前が前方一致なだけの別ディレクトリに一致しない", not path_match("Apple/A.swift", "App/"))
    check("path: glob は basename にも一致", path_match("functions/.env.local", "*.env*"))
    check("path: ファイル名は完全一致", path_match("project.yml", "project.yml") and not path_match("project.yml.bak", "project.yml"))
    check("classify: scope 内は writable", classify_path(c, "App/A.swift") == "writable")
    check("classify: scope 外は out-of-scope", classify_path(c, "Other/A.swift") == "out-of-scope")
    check("classify: forbid は scope に勝つ", classify_path({"scope": {"paths": ["infra/"]}, "forbid": {"paths": ["infra/"]}}, "infra/a.tf") == "forbid")
    check("classify: 契約自身は forbid", classify_path(c, "oneshot.yaml") == "forbid" and classify_path(c, "oneshot/state.json") == "forbid")
    check("classify: oneshot/plan.md は書いてよい", classify_path(c, "oneshot/plan.md") == "writable")
    check("classify: gate_config は gate", classify_path(c, ".claude/harness-targets.json") == "gate")

    check("count: group の和", count_from_output(r"Tests:\s+(\d+) passed", "Tests: 3 passed\nTests: 4 passed") == 7)
    check("count: group 無しは一致の個数", count_from_output(r"(?m)^ok\s", "ok a\nok b\nFAIL c") == 2)
    check("count: 一致なしは None（0 と書かない）", count_from_output(r"Tests:\s+(\d+) passed", "no tests") is None)
    check("count: 0 件は 0（None と区別）", count_from_output(r"Tests:\s+(\d+) passed", "Tests: 0 passed") == 0)
    check("count: 読めない正規表現は None", count_from_output("(", "x") is None)

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        check("state: 無ければ None", load_state(d) is None)
        save_state(d, {"status": "running", "dod": []})
        check("state: 書いて読める", (load_state(d) or {}).get("status") == "running")
        check("state: tmp を残さない", not os.path.exists(state_path(d) + ".tmp"))
        check("relpath: root の外は None", relpath(d, "/etc/passwd") is None)
        check("relpath: 配下は相対", relpath(d, os.path.join(d, "a", "b.txt")) == "a/b.txt")
    import calendar
    check("parse_iso: Z つき", parse_iso("2026-09-25T00:00:00Z") == float(calendar.timegm((2026, 9, 25, 0, 0, 0))))
    check("parse_iso: 読めないと None", parse_iso("not a date") is None)

    print("\n検査 %d 件: 合格 %d / 不合格 %d" % (ok + ng, ok, ng))
    if ng:
        return 1
    print("✅ PASS — 契約・パス・件数・state を 1 つの読み方で扱う")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        sys.exit(_self_test())
    print(__doc__)
    sys.exit(2)
