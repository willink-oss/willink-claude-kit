#!/usr/bin/env python3
"""harness-lint.py — エージェントハーネス資産を lint する単一ファイル CLI

検査対象リポジトリの常駐・観測資産（CLAUDE.md / rules / skills / hooks / memory）を
機械的に検査し、改善候補（圧縮・昇格・重複・リンク切れ・skill 肥大・フック UX 等）を
列挙する観測ツール。月次のハーネスレビューの一次資料になる。

対象（既定・--root からの相対 / 個別 override 可）:
  - ルート直下 CLAUDE.md
  - .claude/rules/*.md（常駐ルール 8 本）           … --rules-dir
  - .claude/skills/*/SKILL.md（24 本）              … --skills-dir
  - fail-closed フック 4 本（pre-bash-safety / pre-file-protect /
    pre-commit-quality / pre-commit-shell-lint）    … <root>/.claude/hooks
  - メモリ: ~/.claude/projects/<cwd スラッグ>/memory（実行時に導出）
    （MEMORY.md 索引 + 個別 *.md）                    … --memory-dir / PH_MEMORY_DIR

設計原則（原則 P1 自己申告禁止・空≠ゼロ件）:
  - 対象が存在しない場合は「対象無・観測継続」を印字し exit0（欠如は失敗でない）。
  - 通常実行は観測なので既定 exit0。--check を付けた時のみ「対象あり かつ 違反>0」で
    exit1 を返す（対象欠如は --check でも exit0）。
  - --self-test は temp fixture で hermetic に検証し実源に一切触れない。
    ハードコード成功を禁じ、各サブで最低 (a) 違反あり fixture の検出 と
    (b) 対象欠如の観測継続 exit0 を assert する。全 pass=exit0 / 1 つでも fail=exit1。
  - 依存は Python3 標準ライブラリのみ。macOS/BSD 互換（grep -P 非使用）。

サブコマンド（12・各: 人間可読 + --json・--check で違反>0→exit1）:
  rule-promote      .claude/rules/ の自然言語ルール行数集計 + common-mistakes.md の
                    advisory 記述から H3(blocking) 昇格候補を列挙
  resident-slim     CLAUDE.md + rules の常駐行数を集計し圧縮候補（行数上位）を出す
  cache-churn       git log で常駐 prefix の直近 N 日編集回数 = cache 破壊度を報告
  skill-desc        各 SKILL.md の frontmatter(name/description) 有無・長さ・
                    トリガー語彙有無・曖昧語を検査
  subagent-overuse  transcript から単一 Read/1-2 Grep での Agent 過剰起動を数える
  trigger-dedup     SKILL.md description のトリガー語彙を抽出し skill 間衝突を検出
  memory-distill    MEMORY.md 索引と memory/*.md を突合し 孤児/リンク切れ/重複 を検出
  rule-dedup        CLAUDE.md と rules 間の重複行/近似重複を検出
  tool-selection    transcript から直接 Read/Grep で足る Agent 誤用を検出
  hook-alt          fail-closed フックの block メッセージに代替/回避策提示があるか検査
  link-sweep        rules/skills md の内部ファイル参照/相対パスの死活を検査し broken 列挙
  skill-size        各 SKILL.md の行数を測り過大(既定>150行)skill を分割候補として出す

共通オプション:
  --json            機械可読出力
  --check           対象あり かつ 違反>0 で exit1（対象欠如は exit0）
  --root DIR        repo ルート差し替え（既定 = このスクリプトの親の親）
  --rules-dir DIR   .claude/rules 差し替え
  --skills-dir DIR  .claude/skills 差し替え
  --memory-dir DIR  memory ディレクトリ差し替え
  --transcript F    単一 transcript jsonl（subagent-overuse / tool-selection 用）
  --projects-dir D  ~/.claude/projects 差し替え（複数 jsonl 走査）
  --days N          cache-churn の遡及日数（既定 30）
  --max-lines N     resident-slim / skill-size の閾値（既定 200 / 150）

usage:
  harness-lint.py <sub>                 # 人間可読サマリ
  harness-lint.py <sub> --json          # JSON
  harness-lint.py <sub> --check         # 違反>0 で exit1
  harness-lint.py <sub> --self-test     # そのサブだけ hermetic 検証（exit0/1）
  harness-lint.py --self-test           # 全 12 サブを検証（全 pass で exit0）
"""

import glob
import json
import os
import re
import subprocess
import sys
import tempfile

import os as _os_ph
import sys as _sys_ph
_sys_ph.path.insert(0, _os_ph.path.dirname(_os_ph.path.abspath(__file__)))
import _phroot  # harness: 検査対象ルート解決

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = _phroot.target_root()


def _default_memory_dir(root=None):
    """Claude Code の project memory ディレクトリを実行環境から導出する。

    Claude Code は cwd の絶対パスの `/` を `-` に置換したものを
    `~/.claude/projects/` 配下のディレクトリ名に使う。ここを固定文字列で
    持つと配布先で必ず外れるため、実行時に導出する。
    `PH_MEMORY_DIR` を置けばそれが最優先（`--memory-dir` はさらに優先）。
    """
    env = os.environ.get("PH_MEMORY_DIR")
    if env:
        return os.path.expanduser(env)
    base = os.path.abspath(root or os.getcwd())
    slug = base.replace(os.sep, "-")
    return os.path.expanduser(os.path.join("~/.claude/projects", slug, "memory"))


DEFAULT_MEMORY_DIR = _default_memory_dir()
FAILCLOSED_HOOKS = [
    "pre-bash-safety.sh",
    "pre-file-protect.sh",
    "pre-commit-quality.sh",
    "pre-commit-shell-lint.sh",
]


# =============================================================
# 共通ヘルパ
# =============================================================
def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _lines(path):
    txt = _read(path)
    if txt is None:
        return None
    return txt.splitlines()


def _observe(sub, note, extra=None):
    """対象欠如時の観測継続レコード（target_present=False・違反 0）。"""
    rec = {"sub": sub, "target_present": False, "violations": 0,
           "items": [], "summary": "対象無・観測継続 — {}".format(note)}
    if extra:
        rec.update(extra)
    return rec


def _mkfixture():
    return tempfile.mkdtemp(prefix="harness-lint-st-")


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _bullet_lines(lines):
    """markdown 箇条書き（自然言語ルール行）のみ返す。表行/見出し/引用は除外。"""
    out = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith(("#", ">", "|", "```")):
            continue
        if s.startswith(("- ", "* ", "+ ")) or re.match(r"^\d+\.\s", s):
            out.append(s)
    return out


def _norm_tokens(text):
    """近似重複判定用の正規化トークン集合（記号除去・小文字化）。"""
    t = text.lower()
    t = re.sub(r"[`*_#>|\-\[\]()。、，,.:：;；!！?？/\\]", " ", t)
    toks = [w for w in t.split() if len(w) >= 2]
    return set(toks)


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# =============================================================
# transcript パーサ（subagent-overuse / tool-selection 共通）
# =============================================================
def _content_blocks(rec):
    m = rec.get("message")
    if not isinstance(m, dict):
        return []
    c = m.get("content")
    if isinstance(c, list):
        return c
    return []


def _iter_transcript(paths):
    """複数 transcript jsonl のレコードを逐次 yield。"""
    for p in paths:
        txt = _read(p)
        if txt is None:
            continue
        for line in txt.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(rec, dict):
                yield rec


def _agent_launches(paths):
    """非 sidechain の Agent/Task 起動を {desc,prompt,text} で返す。"""
    launches = []
    for rec in _iter_transcript(paths):
        if rec.get("isSidechain"):
            continue
        for b in _content_blocks(rec):
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") in ("Agent", "Task"):
                inp = b.get("input") or {}
                desc = str(inp.get("description", ""))
                prompt = str(inp.get("prompt", ""))
                launches.append({"description": desc, "prompt": prompt,
                                 "text": (desc + " " + prompt).strip()})
    return launches


def _transcript_paths(opts):
    """--transcript 優先、無ければ --projects-dir/既定 の *.jsonl。存在するもののみ。"""
    if opts.get("transcript"):
        p = opts["transcript"]
        return [p] if os.path.exists(p) else []
    pdir = opts.get("projects_dir")
    if not pdir:
        return []
    if not os.path.isdir(pdir):
        return []
    return sorted(glob.glob(os.path.join(pdir, "**", "*.jsonl"), recursive=True))


# heuristic 分類器（documented heuristic・過検出を避け保守的に）
OVERUSE_RE = re.compile(
    r"(single file|one file|1 file|単一\s?ファイル|1\s?ファイル|"
    r"read (the )?single|just read|trivial task|1〜2\s?回)", re.I)
TOOLSEL_RE = re.compile(
    r"(grep for|run grep|use grep|single grep|1-2 grep|glob for|use glob|"
    r"read the file|cat the file|find the file)", re.I)


# =============================================================
# path 解決
# =============================================================
def resolve(opts):
    root = opts["root"]
    rules = opts["rules_dir"] or os.path.join(root, ".claude", "rules")
    skills = opts["skills_dir"] or os.path.join(root, ".claude", "skills")
    memory = opts["memory_dir"] or DEFAULT_MEMORY_DIR
    hooks = opts["hooks_dir"] or os.path.join(root, ".claude", "hooks")
    claudemd = os.path.join(root, "CLAUDE.md")
    return {"root": root, "rules": rules, "skills": skills,
            "memory": memory, "hooks": hooks, "claudemd": claudemd}


def _skill_files(skills_dir):
    if not os.path.isdir(skills_dir):
        return None
    return sorted(glob.glob(os.path.join(skills_dir, "*", "SKILL.md")))


def _rule_files(rules_dir):
    if not os.path.isdir(rules_dir):
        return None
    return sorted(glob.glob(os.path.join(rules_dir, "*.md")))


# =============================================================
# 1. rule-promote
# =============================================================
RECUR_KW = ["再発", "繰り返", "習慣", "毎回", "何度も", "recur", "again and again", "又"]


def run_rule_promote(opts):
    p = resolve(opts)
    rule_files = _rule_files(p["rules"])
    if rule_files is None:
        return _observe("rule-promote", "rules dir 無: {}".format(p["rules"]))
    total_rules = 0
    per_file = []
    for f in rule_files:
        n = len(_bullet_lines(_lines(f) or []))
        total_rules += n
        per_file.append({"file": os.path.basename(f), "rule_lines": n})
    # H3 昇格候補: common-mistakes.md の advisory 記述で再発語を含む bullet
    cm = os.path.join(p["rules"], "common-mistakes.md")
    candidates = []
    if os.path.exists(cm):
        for b in _bullet_lines(_lines(cm) or []):
            low = b.lower()
            if "advisory" in low and any(k in b or k in low for k in RECUR_KW):
                candidates.append(b[:160])
    return {"sub": "rule-promote", "target_present": True,
            "violations": len(candidates), "items": candidates,
            "total_rule_lines": total_rules, "per_file": per_file,
            "summary": "rule 行 {} / 昇格候補 {}".format(total_rules, len(candidates))}


def render_rule_promote(r):
    if not r["target_present"]:
        return r["summary"]
    out = ["[rule-promote] {}".format(r["summary"])]
    for pf in r["per_file"]:
        out.append("  {:>3}  {}".format(pf["rule_lines"], pf["file"]))
    if r["items"]:
        out.append("H3 昇格候補（advisory×再発）:")
        for c in r["items"]:
            out.append("  - {}".format(c))
    else:
        out.append("H3 昇格候補: なし")
    return "\n".join(out)


def selftest_rule_promote():
    fails = []
    d = _mkfixture()
    try:
        rules = os.path.join(d, "rules")
        _write(os.path.join(rules, "a.md"), "# t\n- ルール1\n- ルール2\n")
        _write(os.path.join(rules, "common-mistakes.md"),
               "# cm\n- **X** — advisory が再発するので H3 化を検討\n- **Y** — 普通のルール\n")
        opts = _mkopts(root=d, rules_dir=rules)
        r = run_rule_promote(opts)
        if not r["target_present"]:
            fails.append("(a) target_present=False")
        if r["violations"] != 1:
            fails.append("(a) 昇格候補 期待1 実際{}".format(r["violations"]))
        if r["total_rule_lines"] < 2:
            fails.append("(a) rule 行集計不足 {}".format(r["total_rule_lines"]))
        # (b) 対象欠如
        opts2 = _mkopts(root=d, rules_dir=os.path.join(d, "nope"))
        r2 = run_rule_promote(opts2)
        if r2["target_present"] or r2["violations"] != 0:
            fails.append("(b) 対象欠如で観測継続にならない")
    finally:
        _rmtree(d)
    return fails


# =============================================================
# 2. resident-slim
# =============================================================
def run_resident_slim(opts):
    p = resolve(opts)
    rule_files = _rule_files(p["rules"])
    files = []
    if os.path.exists(p["claudemd"]):
        files.append(p["claudemd"])
    if rule_files:
        files.extend(rule_files)
    if not files:
        return _observe("resident-slim", "CLAUDE.md も rules も無")
    thresh = opts["max_lines"] if opts["max_lines"] is not None else 200
    rows = []
    total = 0
    for f in files:
        n = len(_lines(f) or [])
        total += n
        rows.append({"file": os.path.relpath(f, p["root"]), "lines": n,
                     "over": n > thresh})
    rows.sort(key=lambda x: x["lines"], reverse=True)
    viol = [r for r in rows if r["over"]]
    return {"sub": "resident-slim", "target_present": True,
            "violations": len(viol), "items": viol, "rows": rows,
            "total_lines": total, "threshold": thresh,
            "summary": "常駐 {} 行 / {} ファイル / 閾値超 {}".format(
                total, len(rows), len(viol))}


def render_resident_slim(r):
    if not r["target_present"]:
        return r["summary"]
    out = ["[resident-slim] {}".format(r["summary"])]
    for row in r["rows"][:10]:
        mark = "  ⚠" if row["over"] else ""
        out.append("  {:>4}  {}{}".format(row["lines"], row["file"], mark))
    return "\n".join(out)


def selftest_resident_slim():
    fails = []
    d = _mkfixture()
    try:
        _write(os.path.join(d, "CLAUDE.md"), "\n".join("x" for _ in range(300)))
        rules = os.path.join(d, "rules")
        _write(os.path.join(rules, "small.md"), "- a\n- b\n")
        opts = _mkopts(root=d, rules_dir=rules, max_lines=200)
        r = run_resident_slim(opts)
        if r["violations"] != 1:
            fails.append("(a) 閾値超 期待1 実際{}".format(r["violations"]))
        if r["rows"][0]["lines"] != 300:
            fails.append("(a) 上位ソート不正")
        opts2 = _mkopts(root=os.path.join(d, "nope"),
                        rules_dir=os.path.join(d, "nope2"))
        r2 = run_resident_slim(opts2)
        if r2["target_present"] or r2["violations"] != 0:
            fails.append("(b) 対象欠如で観測継続にならない")
    finally:
        _rmtree(d)
    return fails


# =============================================================
# 3. cache-churn
# =============================================================
def _git_commit_files(root, paths, days):
    """git log で paths を触った直近 days 日のコミットを [{files:[...]}] で返す。
    git 無/非 repo/失敗 → None（観測継続）。"""
    try:
        chk = subprocess.run(["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
                             capture_output=True, text=True)
        if chk.returncode != 0 or chk.stdout.strip() != "true":
            return None
        args = ["git", "-C", root, "log", "--since={} days ago".format(days),
                "--name-only", "--pretty=format:%H"]
        if paths:
            args.append("--")
            args.extend(paths)
        out = subprocess.run(args, capture_output=True, text=True)
        if out.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    commits = []
    cur = None
    for ln in out.stdout.splitlines():
        ln = ln.rstrip()
        if not ln:
            continue
        if re.match(r"^[0-9a-f]{7,40}$", ln):
            cur = {"hash": ln, "files": []}
            commits.append(cur)
        elif cur is not None:
            cur["files"].append(ln)
    return commits


def count_churn(commits, watch):
    """commits(=[{files:[...]}]) のうち watch のいずれかを触った回数を path 別に集計。"""
    counts = {w: 0 for w in watch}
    for c in commits:
        touched = set(c.get("files", []))
        for w in watch:
            if any(f == w or f.endswith("/" + w) for f in touched):
                counts[w] += 1
    return counts


def run_cache_churn(opts):
    p = resolve(opts)
    days = opts["days"]
    watch = ["CLAUDE.md", ".claude/rules/common-mistakes.md"]
    commits = _git_commit_files(p["root"], watch, days)
    if commits is None:
        return _observe("cache-churn", "git repo 不在 or git 取得失敗（欠如≠0件）")
    counts = count_churn(commits, watch)
    # churn = 編集回数。閾値超（既定 5 回/30日）を cache 破壊候補とする
    thresh = 5
    viol = [{"path": w, "edits": counts[w]} for w in watch if counts[w] > thresh]
    return {"sub": "cache-churn", "target_present": True,
            "violations": len(viol), "items": viol,
            "counts": counts, "days": days, "threshold": thresh,
            "summary": "直近{}日 churn: {}".format(
                days, ", ".join("{}={}".format(w, counts[w]) for w in watch))}


def render_cache_churn(r):
    if not r["target_present"]:
        return r["summary"]
    out = ["[cache-churn] {}".format(r["summary"])]
    for w, c in r["counts"].items():
        mark = "  ⚠ cache 破壊過多" if c > r["threshold"] else ""
        out.append("  {:>3} 編集  {}{}".format(c, w, mark))
    return "\n".join(out)


def selftest_cache_churn():
    fails = []
    # (a) 純関数 count_churn の検出をハーメティックに検証（git 非依存）
    commits = [
        {"files": ["CLAUDE.md"]},
        {"files": [".claude/rules/common-mistakes.md", "x.md"]},
        {"files": ["CLAUDE.md", "y.md"]},
        {"files": ["unrelated.md"]},
    ]
    counts = count_churn(commits, ["CLAUDE.md", ".claude/rules/common-mistakes.md"])
    if counts["CLAUDE.md"] != 2:
        fails.append("(a) CLAUDE.md churn 期待2 実際{}".format(counts["CLAUDE.md"]))
    if counts[".claude/rules/common-mistakes.md"] != 1:
        fails.append("(a) common-mistakes churn 期待1 実際{}".format(
            counts[".claude/rules/common-mistakes.md"]))
    # 閾値超判定
    big = [{"files": ["CLAUDE.md"]} for _ in range(6)]
    bc = count_churn(big, ["CLAUDE.md"])
    if bc["CLAUDE.md"] != 6:
        fails.append("(a) 閾値超 churn 集計不正 {}".format(bc["CLAUDE.md"]))
    # (b) 非 git ディレクトリ → 観測継続
    d = _mkfixture()
    try:
        opts = _mkopts(root=d)
        r = run_cache_churn(opts)
        if r["target_present"] or r["violations"] != 0:
            fails.append("(b) 非git dir で観測継続にならない")
    finally:
        _rmtree(d)
    return fails


# =============================================================
# 4. skill-desc
# =============================================================
VAGUE_WORDS = ["いい感じ", "うまく", "適当", "など色々", "よしなに", "なんか",
               "various stuff", "etc etc"]


def _parse_frontmatter(text):
    """先頭 --- ... --- の YAML 風 frontmatter を dict で返す（name/description のみ）。"""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block = text[3:end]
    fm = {}
    for ln in block.splitlines():
        m = re.match(r"^([A-Za-z_-]+):\s*(.*)$", ln)
        if m:
            fm[m.group(1).strip()] = m.group(2).strip()
    return fm


def _skill_desc_issues(text):
    issues = []
    fm = _parse_frontmatter(text)
    if fm is None:
        return ["frontmatter 無"]
    if not fm.get("name"):
        issues.append("name 無")
    desc = fm.get("description", "")
    if not desc:
        issues.append("description 無")
    else:
        if len(desc) < 20:
            issues.append("description 短すぎ({}字)".format(len(desc)))
        if len(desc) > 600:
            issues.append("description 長すぎ({}字)".format(len(desc)))
        if "トリガー" not in desc and "trigger" not in desc.lower():
            issues.append("トリガー語彙 無")
        for w in VAGUE_WORDS:
            if w in desc:
                issues.append("曖昧語 '{}'".format(w))
    return issues


def run_skill_desc(opts):
    p = resolve(opts)
    skills = _skill_files(p["skills"])
    if not skills:
        return _observe("skill-desc", "skills dir 無 or SKILL.md 0 件: {}".format(p["skills"]))
    items = []
    for f in skills:
        txt = _read(f) or ""
        iss = _skill_desc_issues(txt)
        if iss:
            items.append({"skill": os.path.basename(os.path.dirname(f)), "issues": iss})
    return {"sub": "skill-desc", "target_present": True,
            "violations": len(items), "items": items, "total": len(skills),
            "summary": "{} skill 中 {} 件に指摘".format(len(skills), len(items))}


def render_skill_desc(r):
    if not r["target_present"]:
        return r["summary"]
    out = ["[skill-desc] {}".format(r["summary"])]
    for it in r["items"]:
        out.append("  {} — {}".format(it["skill"], "; ".join(it["issues"])))
    if not r["items"]:
        out.append("  指摘なし")
    return "\n".join(out)


def selftest_skill_desc():
    fails = []
    d = _mkfixture()
    try:
        skills = os.path.join(d, "skills")
        # good skill
        _write(os.path.join(skills, "good", "SKILL.md"),
               "---\nname: good\ndescription: 十分な長さの説明。トリガー語彙: foo, bar, baz\n---\n# g\n")
        # bad skill: description 無 & frontmatter に name のみ
        _write(os.path.join(skills, "bad", "SKILL.md"),
               "---\nname: bad\n---\n# b\n本文\n")
        opts = _mkopts(root=d, skills_dir=skills)
        r = run_skill_desc(opts)
        if r["violations"] != 1:
            fails.append("(a) 指摘 期待1 実際{}".format(r["violations"]))
        if r["violations"] and r["items"][0]["skill"] != "bad":
            fails.append("(a) 誤検出（good を指摘）")
        opts2 = _mkopts(root=d, skills_dir=os.path.join(d, "nope"))
        r2 = run_skill_desc(opts2)
        if r2["target_present"] or r2["violations"] != 0:
            fails.append("(b) 対象欠如で観測継続にならない")
    finally:
        _rmtree(d)
    return fails


# =============================================================
# 5. subagent-overuse
# =============================================================
def run_subagent_overuse(opts):
    paths = _transcript_paths(opts)
    if not paths:
        return _observe("subagent-overuse",
                        "transcript 源無（--transcript / --projects-dir）")
    launches = _agent_launches(paths)
    suspects = [l for l in launches if OVERUSE_RE.search(l["text"])]
    items = [{"description": s["description"][:80],
              "why": "trivial-scope 委譲 heuristic"} for s in suspects]
    return {"sub": "subagent-overuse", "target_present": True,
            "violations": len(suspects), "items": items,
            "total_launches": len(launches),
            "summary": "Agent 起動 {} / 過剰起動疑い {}".format(
                len(launches), len(suspects))}


def render_subagent_overuse(r):
    if not r["target_present"]:
        return r["summary"]
    out = ["[subagent-overuse] {}".format(r["summary"])]
    for it in r["items"]:
        out.append("  ⚠ {} ({})".format(it["description"], it["why"]))
    if not r["items"]:
        out.append("  過剰起動疑いなし")
    return "\n".join(out)


def _write_transcript(path, records):
    _write(path, "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n")


def selftest_subagent_overuse():
    fails = []
    d = _mkfixture()
    try:
        tp = os.path.join(d, "t.jsonl")
        _write_transcript(tp, [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Agent", "input": {
                    "description": "trivial read",
                    "prompt": "just read the single file /a/b.md and summarize"}}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Agent", "input": {
                    "description": "deep audit",
                    "prompt": "explore 20 files across the repo and report architecture"}}]}},
        ])
        opts = _mkopts(root=d, transcript=tp)
        r = run_subagent_overuse(opts)
        if r["total_launches"] != 2:
            fails.append("(a) 起動数 期待2 実際{}".format(r["total_launches"]))
        if r["violations"] != 1:
            fails.append("(a) 過剰起動 期待1 実際{}".format(r["violations"]))
        opts2 = _mkopts(root=d, transcript=os.path.join(d, "none.jsonl"))
        r2 = run_subagent_overuse(opts2)
        if r2["target_present"] or r2["violations"] != 0:
            fails.append("(b) 源無で観測継続にならない")
    finally:
        _rmtree(d)
    return fails


# =============================================================
# 6. trigger-dedup
# =============================================================
def _extract_triggers(desc):
    """description の 'トリガー語彙: a, b, c' から語彙リストを返す。"""
    m = re.search(r"トリガー語彙[:：]\s*(.+)$", desc)
    if not m:
        return []
    tail = m.group(1)
    parts = re.split(r"[,、，]", tail)
    return [p.strip() for p in parts if p.strip()]


def run_trigger_dedup(opts):
    p = resolve(opts)
    skills = _skill_files(p["skills"])
    if not skills:
        return _observe("trigger-dedup", "skills dir 無 or SKILL.md 0 件")
    owners = {}  # trigger -> set(skill)
    for f in skills:
        name = os.path.basename(os.path.dirname(f))
        fm = _parse_frontmatter(_read(f) or "") or {}
        for t in _extract_triggers(fm.get("description", "")):
            owners.setdefault(t, set()).add(name)
    collisions = [{"trigger": t, "skills": sorted(s)}
                  for t, s in sorted(owners.items()) if len(s) >= 2]
    return {"sub": "trigger-dedup", "target_present": True,
            "violations": len(collisions), "items": collisions,
            "total_triggers": len(owners),
            "summary": "トリガー語 {} 個 / 衝突 {}".format(len(owners), len(collisions))}


def render_trigger_dedup(r):
    if not r["target_present"]:
        return r["summary"]
    out = ["[trigger-dedup] {}".format(r["summary"])]
    for c in r["items"]:
        out.append("  ⚠ '{}' → {}".format(c["trigger"], ", ".join(c["skills"])))
    if not r["items"]:
        out.append("  衝突なし")
    return "\n".join(out)


def selftest_trigger_dedup():
    fails = []
    d = _mkfixture()
    try:
        skills = os.path.join(d, "skills")
        _write(os.path.join(skills, "a", "SKILL.md"),
               "---\nname: a\ndescription: A の説明。トリガー語彙: 共有語, 固有A\n---\n")
        _write(os.path.join(skills, "b", "SKILL.md"),
               "---\nname: b\ndescription: B の説明。トリガー語彙: 共有語, 固有B\n---\n")
        opts = _mkopts(root=d, skills_dir=skills)
        r = run_trigger_dedup(opts)
        if r["violations"] != 1:
            fails.append("(a) 衝突 期待1 実際{}".format(r["violations"]))
        if r["violations"] and r["items"][0]["trigger"] != "共有語":
            fails.append("(a) 衝突語不一致")
        opts2 = _mkopts(root=d, skills_dir=os.path.join(d, "nope"))
        r2 = run_trigger_dedup(opts2)
        if r2["target_present"] or r2["violations"] != 0:
            fails.append("(b) 対象欠如で観測継続にならない")
    finally:
        _rmtree(d)
    return fails


# =============================================================
# 7. memory-distill
# =============================================================
def _parse_memory_index(text):
    """MEMORY.md の '[file](file) — desc' リンクを [(target, desc)] で返す。"""
    entries = []
    for ln in text.splitlines():
        m = re.search(r"\[[^\]]+\]\(([^)]+)\)\s*(?:[—-]\s*(.*))?$", ln.strip())
        if m:
            target = m.group(1).strip()
            desc = (m.group(2) or "").strip()
            entries.append((target, desc))
    return entries


def run_memory_distill(opts):
    p = resolve(opts)
    mdir = p["memory"]
    index = os.path.join(mdir, "MEMORY.md")
    if not os.path.isdir(mdir) or not os.path.exists(index):
        return _observe("memory-distill", "memory dir/MEMORY.md 無: {}".format(mdir))
    entries = _parse_memory_index(_read(index) or "")
    indexed = {}
    dup_index = []
    for target, desc in entries:
        base = os.path.basename(target)
        if base in indexed:
            dup_index.append(base)
        indexed[base] = desc
    on_disk = {os.path.basename(f) for f in glob.glob(os.path.join(mdir, "*.md"))}
    on_disk.discard("MEMORY.md")
    broken = sorted(b for b in indexed if b not in on_disk)   # 索引にあるが実体無
    orphans = sorted(f for f in on_disk if f not in indexed)  # 実体あるが未索引
    # 近似重複: 説明文が高類似のペア
    dup_desc = []
    items_list = [(b, d) for b, d in indexed.items() if d]
    for i in range(len(items_list)):
        for j in range(i + 1, len(items_list)):
            if _jaccard(_norm_tokens(items_list[i][1]),
                        _norm_tokens(items_list[j][1])) >= 0.6:
                dup_desc.append([items_list[i][0], items_list[j][0]])
    viol = len(broken) + len(orphans) + len(dup_index) + len(dup_desc)
    return {"sub": "memory-distill", "target_present": True, "violations": viol,
            "items": {"broken_links": broken, "orphans": orphans,
                      "dup_index": sorted(set(dup_index)), "dup_desc": dup_desc},
            "indexed": len(indexed), "on_disk": len(on_disk),
            "summary": "索引{} / 実体{} / broken{} orphan{} dup{}".format(
                len(indexed), len(on_disk), len(broken), len(orphans),
                len(dup_index) + len(dup_desc))}


def render_memory_distill(r):
    if not r["target_present"]:
        return r["summary"]
    out = ["[memory-distill] {}".format(r["summary"])]
    it = r["items"]
    if it["broken_links"]:
        out.append("  索引リンク切れ: {}".format(", ".join(it["broken_links"])))
    if it["orphans"]:
        out.append("  孤児(未索引): {}".format(", ".join(it["orphans"])))
    if it["dup_index"]:
        out.append("  索引重複: {}".format(", ".join(it["dup_index"])))
    for pair in it["dup_desc"]:
        out.append("  説明近似重複: {} ~ {}".format(pair[0], pair[1]))
    if r["violations"] == 0:
        out.append("  整合 OK")
    return "\n".join(out)


def selftest_memory_distill():
    fails = []
    d = _mkfixture()
    try:
        mdir = os.path.join(d, "memory")
        _write(os.path.join(mdir, "MEMORY.md"),
               "# Index\n- [a.md](a.md) — 説明A\n- [ghost.md](ghost.md) — 実体無\n")
        _write(os.path.join(mdir, "a.md"), "A\n")
        _write(os.path.join(mdir, "orphan.md"), "誰も索引していない\n")
        opts = _mkopts(root=d, memory_dir=mdir)
        r = run_memory_distill(opts)
        # broken: ghost.md(1) + orphan: orphan.md(1) = 2
        if r["violations"] < 2:
            fails.append("(a) 違反 期待>=2 実際{}".format(r["violations"]))
        if "ghost.md" not in r["items"]["broken_links"]:
            fails.append("(a) broken link 未検出")
        if "orphan.md" not in r["items"]["orphans"]:
            fails.append("(a) orphan 未検出")
        opts2 = _mkopts(root=d, memory_dir=os.path.join(d, "nope"))
        r2 = run_memory_distill(opts2)
        if r2["target_present"] or r2["violations"] != 0:
            fails.append("(b) 対象欠如で観測継続にならない")
    finally:
        _rmtree(d)
    return fails


# =============================================================
# 8. rule-dedup
# =============================================================
def run_rule_dedup(opts):
    p = resolve(opts)
    if not os.path.exists(p["claudemd"]):
        return _observe("rule-dedup", "CLAUDE.md 無: {}".format(p["claudemd"]))
    rule_files = _rule_files(p["rules"]) or []
    claude_bullets = [(os.path.basename(p["claudemd"]), b)
                      for b in _bullet_lines(_lines(p["claudemd"]) or [])]
    rule_bullets = []
    for f in rule_files:
        for b in _bullet_lines(_lines(f) or []):
            rule_bullets.append((os.path.basename(f), b))
    dups = []
    for cf, cb in claude_bullets:
        ct = _norm_tokens(cb)
        for rf, rb in rule_bullets:
            j = _jaccard(ct, _norm_tokens(rb))
            if j >= 0.7:
                dups.append({"claude": cb[:80], "rule_file": rf,
                             "rule": rb[:80], "jaccard": round(j, 2)})
    return {"sub": "rule-dedup", "target_present": True, "violations": len(dups),
            "items": dups,
            "summary": "CLAUDE.md×rules 近似重複 {} 件".format(len(dups))}


def render_rule_dedup(r):
    if not r["target_present"]:
        return r["summary"]
    out = ["[rule-dedup] {}".format(r["summary"])]
    for it in r["items"][:20]:
        out.append("  ~{} [{}] {}".format(it["jaccard"], it["rule_file"], it["rule"]))
    if not r["items"]:
        out.append("  重複なし")
    return "\n".join(out)


def selftest_rule_dedup():
    fails = []
    d = _mkfixture()
    try:
        shared = "- **force push 禁止** — 必要な場合は社長に事前確認する"
        _write(os.path.join(d, "CLAUDE.md"), "# c\n{}\n- 固有ルール xyz\n".format(shared))
        rules = os.path.join(d, "rules")
        _write(os.path.join(rules, "cm.md"), "# cm\n{}\n- 別ルール abc\n".format(shared))
        opts = _mkopts(root=d, rules_dir=rules)
        r = run_rule_dedup(opts)
        if r["violations"] < 1:
            fails.append("(a) 重複 期待>=1 実際{}".format(r["violations"]))
        opts2 = _mkopts(root=os.path.join(d, "nope"))
        r2 = run_rule_dedup(opts2)
        if r2["target_present"] or r2["violations"] != 0:
            fails.append("(b) 対象欠如で観測継続にならない")
    finally:
        _rmtree(d)
    return fails


# =============================================================
# 9. tool-selection
# =============================================================
def run_tool_selection(opts):
    paths = _transcript_paths(opts)
    if not paths:
        return _observe("tool-selection",
                        "transcript 源無（--transcript / --projects-dir）")
    launches = _agent_launches(paths)
    suspects = [l for l in launches if TOOLSEL_RE.search(l["text"])]
    items = [{"description": s["description"][:80],
              "why": "直接 Read/Grep/Glob で足る委譲 heuristic"} for s in suspects]
    return {"sub": "tool-selection", "target_present": True,
            "violations": len(suspects), "items": items,
            "total_launches": len(launches),
            "summary": "Agent 起動 {} / 直接 tool で足る誤用 {}".format(
                len(launches), len(suspects))}


def render_tool_selection(r):
    if not r["target_present"]:
        return r["summary"]
    out = ["[tool-selection] {}".format(r["summary"])]
    for it in r["items"]:
        out.append("  ⚠ {} ({})".format(it["description"], it["why"]))
    if not r["items"]:
        out.append("  誤用なし")
    return "\n".join(out)


def selftest_tool_selection():
    fails = []
    d = _mkfixture()
    try:
        tp = os.path.join(d, "t.jsonl")
        _write_transcript(tp, [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Agent", "input": {
                    "description": "wrong tool",
                    "prompt": "use grep to find TODO in src/app.py and report"}}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Agent", "input": {
                    "description": "legit explore",
                    "prompt": "design the migration plan across services"}}]}},
        ])
        opts = _mkopts(root=d, transcript=tp)
        r = run_tool_selection(opts)
        if r["violations"] != 1:
            fails.append("(a) 誤用 期待1 実際{}".format(r["violations"]))
        opts2 = _mkopts(root=d, transcript=os.path.join(d, "none.jsonl"))
        r2 = run_tool_selection(opts2)
        if r2["target_present"] or r2["violations"] != 0:
            fails.append("(b) 源無で観測継続にならない")
    finally:
        _rmtree(d)
    return fails


# =============================================================
# 10. hook-alt
# =============================================================
BLOCK_RE = re.compile(r"(BLOCKED|❌|\bblock\b|ブロック)")
ALT_RE = re.compile(
    r"(Alternative:|Action:|Install:|代わりに|を使う|instead|推奨|回避|使ってください)")


def run_hook_alt(opts):
    p = resolve(opts)
    hooks_dir = p["hooks"]
    if not os.path.isdir(hooks_dir):
        return _observe("hook-alt", "hooks dir 無: {}".format(hooks_dir))
    present = [h for h in FAILCLOSED_HOOKS
               if os.path.exists(os.path.join(hooks_dir, h))]
    if not present:
        return _observe("hook-alt", "fail-closed フック 0 件: {}".format(hooks_dir))
    items = []
    for h in present:
        txt = _read(os.path.join(hooks_dir, h)) or ""
        has_block = bool(BLOCK_RE.search(txt))
        has_alt = bool(ALT_RE.search(txt))
        if has_block and not has_alt:
            items.append({"hook": h, "issue": "block あり・代替/回避策の提示なし"})
    return {"sub": "hook-alt", "target_present": True, "violations": len(items),
            "items": items, "checked": len(present),
            "summary": "fail-closed {} 本中 {} 本が代替提示欠如".format(
                len(present), len(items))}


def render_hook_alt(r):
    if not r["target_present"]:
        return r["summary"]
    out = ["[hook-alt] {}".format(r["summary"])]
    for it in r["items"]:
        out.append("  ⚠ {} — {}".format(it["hook"], it["issue"]))
    if not r["items"]:
        out.append("  全 fail-closed フックが代替提示あり")
    return "\n".join(out)


def selftest_hook_alt():
    fails = []
    d = _mkfixture()
    try:
        hooks = os.path.join(d, "hooks")
        # good: block + Alternative
        _write(os.path.join(hooks, "pre-bash-safety.sh"),
               "#!/bin/bash\necho 'BLOCKED: no' >&2\necho 'Alternative: use PR' >&2\nexit 2\n")
        # bad: block だが代替提示なし
        _write(os.path.join(hooks, "pre-file-protect.sh"),
               "#!/bin/bash\necho 'BLOCKED: protected file' >&2\nexit 2\n")
        opts = _mkopts(root=d, hooks_dir=hooks)
        r = run_hook_alt(opts)
        if r["violations"] != 1:
            fails.append("(a) 代替欠如 期待1 実際{}".format(r["violations"]))
        if r["violations"] and r["items"][0]["hook"] != "pre-file-protect.sh":
            fails.append("(a) 誤検出（good を指摘）")
        opts2 = _mkopts(root=d, hooks_dir=os.path.join(d, "nope"))
        r2 = run_hook_alt(opts2)
        if r2["target_present"] or r2["violations"] != 0:
            fails.append("(b) 対象欠如で観測継続にならない")
    finally:
        _rmtree(d)
    return fails


# =============================================================
# 11. link-sweep
# =============================================================
PATH_EXT = r"(?:md|sh|py|json|jsonl|ts|tsx|js|yml|yaml|toml|txt)"
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
BACKTICK_RE = re.compile(r"`([^`]+)`")
PATHISH_RE = re.compile(r"^[\w./@-]+\." + PATH_EXT + r"$")
# 明らかなテンプレ placeholder（実ファイルでなく命名パターン）は broken 対象外
PLACEHOLDER_RE = re.compile(r"(YYYY|MM-DD|WNN|NN\b|<[^>]+>|\{[^}]+\})")
# 意図的な外部/実行時参照は broken 対象外:
#  - .claude/logs/ 配下 = gitignore 済ランタイムログ（実行時に生成・repo に無くて正常）
#  - <name>.<tld>/... = cross-repo ポインタ（example.com/... 等・別リポジトリを指す意図的参照）
IGNORE_REF_RE = re.compile(r"(^|/)\.claude/logs/|^[\w-]+\.(com|dev|io|app|jp|net)/")


def _link_candidates(text):
    """md 内の内部ファイル参照候補（http/mailto/anchor/placeholder 除外）を返す。"""
    cands = []
    for m in MD_LINK_RE.finditer(text):
        cands.append(m.group(1).strip())
    for m in BACKTICK_RE.finditer(text):
        tok = m.group(1).strip()
        # 明らかにコマンド列（空白含む）は除外し、path っぽいもののみ
        if " " in tok:
            continue
        cands.append(tok)
    out = []
    for c in cands:
        c0 = c.split("#", 1)[0].strip()
        if not c0:
            continue
        if c0.startswith(("http://", "https://", "mailto:")):
            continue
        if "/" not in c0:
            continue
        if PLACEHOLDER_RE.search(c0):
            continue
        if IGNORE_REF_RE.search(c0):
            continue
        if not PATHISH_RE.match(c0):
            continue
        out.append(c0)
    return out


def _resolve_ref(ref, md_file, root):
    """ref が md_file の隣 or root 相対で存在するか。"""
    base_dir = os.path.dirname(md_file)
    for cand in (os.path.join(base_dir, ref), os.path.join(root, ref)):
        if os.path.exists(os.path.normpath(cand)):
            return True
    return False


def run_link_sweep(opts):
    p = resolve(opts)
    md_files = []
    rf = _rule_files(p["rules"])
    if rf:
        md_files.extend(rf)
    sk = _skill_files(p["skills"])
    if sk:
        md_files.extend(sk)
    if not md_files:
        return _observe("link-sweep", "rules/skills md 0 件")
    broken = []
    checked = 0
    for f in md_files:
        txt = _read(f) or ""
        for ref in _link_candidates(txt):
            checked += 1
            if not _resolve_ref(ref, f, p["root"]):
                broken.append({"file": os.path.relpath(f, p["root"]), "ref": ref})
    return {"sub": "link-sweep", "target_present": True, "violations": len(broken),
            "items": broken, "checked_refs": checked, "files": len(md_files),
            "summary": "md {} 本 / 参照 {} 件 / broken {}".format(
                len(md_files), checked, len(broken))}


def render_link_sweep(r):
    if not r["target_present"]:
        return r["summary"]
    out = ["[link-sweep] {}".format(r["summary"])]
    for it in r["items"][:30]:
        out.append("  ✗ [{}] {}".format(it["file"], it["ref"]))
    if not r["items"]:
        out.append("  broken link なし")
    return "\n".join(out)


def selftest_link_sweep():
    fails = []
    d = _mkfixture()
    try:
        rules = os.path.join(d, "rules")
        # real.md は存在・ghost.md は不在
        _write(os.path.join(rules, "real.md"), "target\n")
        _write(os.path.join(rules, "a.md"),
               "参照 [ok](real.md) と `standards/ghost.md` と外部 https://x.com/y.md "
               "と placeholder `assets/meeting-notes/YYYY-MM-DD_x.md`\n")
        opts = _mkopts(root=d, rules_dir=rules, skills_dir=os.path.join(d, "noskills"))
        r = run_link_sweep(opts)
        # ghost.md のみ broken（placeholder は除外・real.md は存在・http は除外）
        if r["violations"] != 1:
            fails.append("(a) broken 期待1 実際{} items={}".format(
                r["violations"], r["items"]))
        if r["violations"] and "ghost" not in r["items"][0]["ref"]:
            fails.append("(a) broken ref 不一致 {}".format(r["items"]))
        opts2 = _mkopts(root=d, rules_dir=os.path.join(d, "nope"),
                        skills_dir=os.path.join(d, "nope2"))
        r2 = run_link_sweep(opts2)
        if r2["target_present"] or r2["violations"] != 0:
            fails.append("(b) 対象欠如で観測継続にならない")
    finally:
        _rmtree(d)
    return fails


# =============================================================
# 12. skill-size
# =============================================================
def run_skill_size(opts):
    p = resolve(opts)
    skills = _skill_files(p["skills"])
    if not skills:
        return _observe("skill-size", "skills dir 無 or SKILL.md 0 件")
    thresh = opts["max_lines"] if opts["max_lines"] is not None else 150
    rows = []
    for f in skills:
        n = len(_lines(f) or [])
        rows.append({"skill": os.path.basename(os.path.dirname(f)), "lines": n,
                     "over": n > thresh})
    rows.sort(key=lambda x: x["lines"], reverse=True)
    viol = [r for r in rows if r["over"]]
    return {"sub": "skill-size", "target_present": True, "violations": len(viol),
            "items": viol, "rows": rows, "threshold": thresh,
            "summary": "{} skill / 過大(>{}行) {}".format(
                len(rows), thresh, len(viol))}


def render_skill_size(r):
    if not r["target_present"]:
        return r["summary"]
    out = ["[skill-size] {}".format(r["summary"])]
    for row in r["rows"][:10]:
        mark = "  ⚠ 分割候補" if row["over"] else ""
        out.append("  {:>4}  {}{}".format(row["lines"], row["skill"], mark))
    return "\n".join(out)


def selftest_skill_size():
    fails = []
    d = _mkfixture()
    try:
        skills = os.path.join(d, "skills")
        _write(os.path.join(skills, "big", "SKILL.md"),
               "\n".join("line {}".format(i) for i in range(200)))
        _write(os.path.join(skills, "small", "SKILL.md"), "# small\n- a\n")
        opts = _mkopts(root=d, skills_dir=skills, max_lines=150)
        r = run_skill_size(opts)
        if r["violations"] != 1:
            fails.append("(a) 過大 期待1 実際{}".format(r["violations"]))
        if r["rows"][0]["skill"] != "big":
            fails.append("(a) ソート不正")
        opts2 = _mkopts(root=d, skills_dir=os.path.join(d, "nope"))
        r2 = run_skill_size(opts2)
        if r2["target_present"] or r2["violations"] != 0:
            fails.append("(b) 対象欠如で観測継続にならない")
    finally:
        _rmtree(d)
    return fails


# =============================================================
# 登録・引数・main
# =============================================================
SUBCOMMANDS = {
    "rule-promote": (run_rule_promote, render_rule_promote, selftest_rule_promote),
    "resident-slim": (run_resident_slim, render_resident_slim, selftest_resident_slim),
    "cache-churn": (run_cache_churn, render_cache_churn, selftest_cache_churn),
    "skill-desc": (run_skill_desc, render_skill_desc, selftest_skill_desc),
    "subagent-overuse": (run_subagent_overuse, render_subagent_overuse, selftest_subagent_overuse),
    "trigger-dedup": (run_trigger_dedup, render_trigger_dedup, selftest_trigger_dedup),
    "memory-distill": (run_memory_distill, render_memory_distill, selftest_memory_distill),
    "rule-dedup": (run_rule_dedup, render_rule_dedup, selftest_rule_dedup),
    "tool-selection": (run_tool_selection, render_tool_selection, selftest_tool_selection),
    "hook-alt": (run_hook_alt, render_hook_alt, selftest_hook_alt),
    "link-sweep": (run_link_sweep, render_link_sweep, selftest_link_sweep),
    "skill-size": (run_skill_size, render_skill_size, selftest_skill_size),
}
# 決定論のため self-test は登録順で回す
SUB_ORDER = ["rule-promote", "resident-slim", "cache-churn", "skill-desc",
             "subagent-overuse", "trigger-dedup", "memory-distill", "rule-dedup",
             "tool-selection", "hook-alt", "link-sweep", "skill-size"]


def _mkopts(**kw):
    """テスト/内部用の opts 生成。既定値を埋める。"""
    opts = {"sub": None, "json": False, "check": False, "selftest": False,
            "root": REPO_ROOT, "rules_dir": None, "skills_dir": None,
            "memory_dir": None, "hooks_dir": None, "transcript": None,
            "projects_dir": None, "days": 30, "max_lines": None}
    opts.update(kw)
    return opts


def parse_args(argv):
    opts = _mkopts()
    i, positional = 0, []
    while i < len(argv):
        t = argv[i]
        if t == "--json":
            opts["json"] = True
        elif t == "--check":
            opts["check"] = True
        elif t == "--self-test":
            opts["selftest"] = True
        elif t == "--root":
            i += 1; opts["root"] = argv[i]
        elif t == "--rules-dir":
            i += 1; opts["rules_dir"] = argv[i]
        elif t == "--skills-dir":
            i += 1; opts["skills_dir"] = argv[i]
        elif t == "--memory-dir":
            i += 1; opts["memory_dir"] = argv[i]
        elif t == "--hooks-dir":
            i += 1; opts["hooks_dir"] = argv[i]
        elif t == "--transcript":
            i += 1; opts["transcript"] = argv[i]
        elif t == "--projects-dir":
            i += 1; opts["projects_dir"] = argv[i]
        elif t == "--days":
            i += 1; opts["days"] = int(argv[i])
        elif t == "--max-lines":
            i += 1; opts["max_lines"] = int(argv[i])
        elif t in ("-h", "--help"):
            opts["sub"] = "--help"
        elif t.startswith("--"):
            sys.stderr.write("unknown option: {}\n".format(t))
            opts["sub"] = "__error__"
        else:
            positional.append(t)
        i += 1
    if positional:
        opts["sub"] = positional[0]
    return opts


def run_all_selftests():
    print("harness-lint self-test — 全 {} サブコマンド (hermetic fixture)".format(
        len(SUB_ORDER)))
    any_fail = False
    for name in SUB_ORDER:
        _, _, st = SUBCOMMANDS[name]
        fails = st()
        if fails:
            any_fail = True
            print("  FAIL  {}".format(name))
            for f in fails:
                print("        - {}".format(f))
        else:
            print("  PASS  {}".format(name))
    if any_fail:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS ({} / {})".format(len(SUB_ORDER), len(SUB_ORDER)))
    return 0


HELP = __doc__


def main(argv):
    opts = parse_args(argv[1:])
    sub = opts["sub"]

    if sub in ("--help", None) and not opts["selftest"]:
        sys.stdout.write(HELP)
        return 0
    if sub == "__error__":
        return 2

    if opts["selftest"] and sub is None:
        return run_all_selftests()

    if sub not in SUBCOMMANDS:
        sys.stderr.write("unknown subcommand: {}\n".format(sub))
        sys.stderr.write("available: {}\n".format(", ".join(SUB_ORDER)))
        return 2

    run_fn, render_fn, selftest_fn = SUBCOMMANDS[sub]

    if opts["selftest"]:
        fails = selftest_fn()
        if fails:
            sys.stderr.write("{} self-test: FAIL\n".format(sub))
            for f in fails:
                sys.stderr.write("  - {}\n".format(f))
            return 1
        sys.stdout.write("{} self-test: PASS\n".format(sub))
        return 0

    result = run_fn(opts)
    if opts["json"]:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
    else:
        sys.stdout.write(render_fn(result) + "\n")

    # --check: 対象あり かつ 違反>0 で exit1（対象欠如は exit0）
    if opts["check"] and result.get("target_present") and result.get("violations", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
