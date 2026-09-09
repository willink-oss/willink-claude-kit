#!/usr/bin/env python3
"""agentlog.py — crew 観測エンジン（ローカルログ + 成果物の解析 CLI・単一ファイル）

crew が日々吐く gitignore 済みローカルログ（`.claude/logs/*`）と Claude Code の
会話トランスクリプト（`~/.claude/projects/<proj>/<uuid>.jsonl`）、および repo の
git log / ナレッジ / routine registry を横断して「ハーネスの効き / 事故の予兆」を
機械的に集計する。月次 /harness-review・自走ループ監視の一次資料になる。

重要な設計原則（空≠ゼロ件・原則 P1 自己申告禁止）:
  - ログ源は gitignore 済でローカルのみ・fresh checkout には無い。**源が無い時は
    「観測継続（データ無）」と印字し exit 0**（壊れ扱いにしない）。取得失敗を
    「0 件」と断定しない。
  - `--self-test` は temp fixture で hermetic に検証し、実源には一切触れない。
    ハードコード成功を禁じ、正常 fixture の期待集計 (a) と源欠如の観測継続 (b) を
    最低限 assert する。全 pass=exit0 / 1 つでも fail=exit1。

サブコマンド（各: 人間可読サマリ + --json 対応・源欠如は exit0 で観測継続）:
  tool-pattern      tools.jsonl の tool 別呼出頻度・top・隣接連鎖の粗集計
  retry             tools.jsonl の exit_code 非空非0 を失敗/リトライとして集計
  cost              tools.jsonl の日別呼出数（コスト偏重日）
  denials           tools.jsonl(Bash/Edit 異常 exit) + transcript の denied/BLOCKED
  subagent          transcript の Agent 起動件数 + 子 tool_use<=2 過剰起動 heuristic
  context-bloat     transcript の session 内メッセージ数/近似入力量から肥大検出
  session-hygiene   transcript の session 別メッセージ数 + compact 回数
  unverified-claims 状態断定句の直前に live 実測 tool が無い箇所を heuristic 検出
  rework            git log 直近N日の revert/同一ファイル再編集（手戻り）検出
  mistake-tags      mistake-log を根本原因キーワードでタグ分類し再発 top
  routine-drift     routines/registry の各 routine の最終 commit を照合し SLA 超過

共通オプション（テスト容易性の上書き引数）:
  --json            機械可読出力
  --log-dir DIR     .claude/logs の差し替え（tools/advisory/review-gate）
  --transcript F    単一トランスクリプト jsonl を対象にする
  --projects-dir D  ~/.claude/projects の差し替え（複数 jsonl を走査）
  --root DIR        repo ルートの差し替え（git log / registry / mistake-log）
  --days N          rework/routine-drift の遡及日数（既定 30）

usage:
  agentlog.py <sub>                    # 人間可読サマリ
  agentlog.py <sub> --json             # JSON
  agentlog.py <sub> --self-test        # そのサブコマンドだけ hermetic 検証（exit0/1）
  agentlog.py --self-test              # 全 11 サブコマンドを検証（全 pass で exit0）
"""

import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

import os as _os_ph
import sys as _sys_ph
_sys_ph.path.insert(0, _os_ph.path.dirname(_os_ph.path.abspath(__file__)))
import _phroot  # proof-harness: 検査対象ルート解決

REPO_ROOT = _phroot.target_root()
DEFAULT_PROJECTS = os.path.expanduser("~/.claude/projects")
JST = timezone(timedelta(hours=9))

# 状態断定句（unverified-claims）— 日本語 + 英語
CLAIM_PATTERNS = [
    "マージ済", "マージした", "マージ完了", "デプロイ済", "デプロイした", "デプロイ完了",
    "反映済", "反映しました", "公開済", "公開しました", "リリース済", "完了しました",
    "対応済", "成功しました", "解消しました", "修正しました", "merged", "deployed",
    "completed", "shipped", "is now live", "已完成",
]
# live 実測とみなす Bash トークン（unverified-claims / denials）
LIVE_TOKENS = ["gh ", "gh\t", "curl", "aws ", "aws\t", "git "]

# routine cadence → SLA（漂流とみなす経過日数の上限。catch-up の猶予込み）
ROUTINE_SLA_DAYS = {
    "daily": 2, "weekdays": 2,
    "weekly-mon": 9, "weekly-fri": 9, "weekly-sun": 9,
    "monthly": 35,
}

# context-bloat の肥大しきい値（どちらか超過で flag）
BLOAT_MSG_THRESHOLD = 200
BLOAT_CHAR_THRESHOLD = 400_000


# --------------------------------------------------------------------------
# 低レベルユーティリティ
# --------------------------------------------------------------------------
def read_jsonl(path):
    """1 行 1 JSON を読む。空行/壊れ行はスキップ（malformed 数を第2要素で返す）。"""
    records, malformed = [], 0
    if not path or not os.path.exists(path):
        return records, malformed
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                malformed += 1
                continue
            if isinstance(rec, dict):
                records.append(rec)
            else:
                malformed += 1
    return records, malformed


def tool_event_files(log_dir):
    """<log-dir>/YYYY-MM-DD-tools.jsonl を日付昇順で返す。"""
    if not log_dir or not os.path.isdir(log_dir):
        return []
    return sorted(glob.glob(os.path.join(log_dir, "*-tools.jsonl")))


def load_tool_events(log_dir):
    """全 *-tools.jsonl を時系列連結して返す。"""
    events = []
    for f in tool_event_files(log_dir):
        recs, _ = read_jsonl(f)
        events.extend(recs)
    return events


def transcript_paths(opts):
    """対象トランスクリプト jsonl のパス列を返す（源無しは空列）。"""
    if opts.get("transcript"):
        p = opts["transcript"]
        return [p] if os.path.exists(p) else []
    pd = opts.get("projects_dir") or DEFAULT_PROJECTS
    if not os.path.isdir(pd):
        return []
    return sorted(glob.glob(os.path.join(pd, "*", "*.jsonl")))


def load_transcript(path):
    recs, _ = read_jsonl(path)
    return recs


def msg_blocks(rec):
    """transcript レコードの content ブロック列を正規化して返す。"""
    m = rec.get("message")
    if not isinstance(m, dict):
        return []
    c = m.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    if isinstance(c, list):
        return [b for b in c if isinstance(b, dict)]
    return []


def block_text(b):
    if b.get("type") in ("text", "thinking"):
        return b.get("text") or b.get("thinking") or ""
    return ""


def approx_block_chars(b):
    t = b.get("type")
    if t in ("text", "thinking"):
        return len(b.get("text") or b.get("thinking") or "")
    if t == "tool_use":
        try:
            return len(json.dumps(b.get("input", {}), ensure_ascii=False))
        except (TypeError, ValueError):
            return 0
    if t == "tool_result":
        c = b.get("content")
        return len(c) if isinstance(c, str) else len(json.dumps(c, ensure_ascii=False, default=str))
    return 0


def is_live_bash(cmd):
    if not isinstance(cmd, str):
        return False
    return any(tok.strip() and (tok in cmd or cmd.startswith(tok.strip() + " ")) for tok in LIVE_TOKENS)


def contains_claim(text):
    low = text.lower()
    for p in CLAIM_PATTERNS:
        if p.lower() in low:
            return p
    return None


def _sorted_desc(counts):
    return sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))


def _run_git(root, args):
    try:
        p = subprocess.run(["git", "-C", root] + args,
                           capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "git not found"


def is_git_repo(root):
    rc, out, _ = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    return rc == 0 and out.strip() == "true"


def today_jst(override=None):
    if override:
        return date.fromisoformat(override)
    return datetime.now(JST).date()


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            if isinstance(r, str):
                fh.write(r + "\n")
            else:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _continue_line(what):
    return "観測継続（データ無）: {} — 源欠如は 0 件と断定しない（exit0）。".format(what)


# ==========================================================================
# 1. tool-pattern
# ==========================================================================
def run_tool_pattern(opts):
    log_dir = opts["log_dir"]
    files = tool_event_files(log_dir)
    events = load_tool_events(log_dir)
    r = {"sub": "tool-pattern", "log_dir": log_dir, "source_files": len(files),
         "source_ok": bool(events), "total": 0, "by_tool": {}, "top_chains": []}
    if not events:
        return r
    by_tool = {}
    chains = {}
    prev = None
    for e in events:
        tool = e.get("tool") or "unknown"
        by_tool[tool] = by_tool.get(tool, 0) + 1
        if prev is not None:
            key = "{} -> {}".format(prev, tool)
            chains[key] = chains.get(key, 0) + 1
        prev = tool
    r["total"] = len(events)
    r["by_tool"] = by_tool
    r["top_chains"] = _sorted_desc(chains)[:5]
    r["source_ok"] = True
    return r


def render_tool_pattern(r):
    out = ["tool-pattern — {}".format(r["log_dir"])]
    if not r["source_ok"]:
        out.append(_continue_line("tools.jsonl 未生成"))
        return "\n".join(out)
    out.append("total tool calls: {} ({} file)".format(r["total"], r["source_files"]))
    out.append("")
    out.append("by tool:")
    for tool, n in _sorted_desc(r["by_tool"]):
        out.append("  {:>5}  {}".format(n, tool))
    if r["top_chains"]:
        out.append("")
        out.append("top chains (隣接連鎖):")
        for key, n in r["top_chains"]:
            out.append("  {:>5}  {}".format(n, key))
    return "\n".join(out)


def selftest_tool_pattern():
    fails = []
    d = tempfile.mkdtemp(prefix="agentlog-tp-")
    try:
        _write_jsonl(os.path.join(d, "2026-07-04-tools.jsonl"), [
            {"ts": "2026-07-04T10:00:00+0900", "tool": "Bash", "matcher": "", "exit_code": ""},
            {"ts": "2026-07-04T10:01:00+0900", "tool": "Read", "matcher": "", "exit_code": ""},
            {"ts": "2026-07-04T10:02:00+0900", "tool": "Bash", "matcher": "", "exit_code": ""},
            {"ts": "2026-07-04T10:03:00+0900", "tool": "Read", "matcher": "", "exit_code": ""},
        ])
        r = run_tool_pattern({"log_dir": d})
        if r["total"] != 4:
            fails.append("total expected 4, got {}".format(r["total"]))
        if r["by_tool"] != {"Bash": 2, "Read": 2}:
            fails.append("by_tool mismatch: {}".format(r["by_tool"]))
        # 連鎖 Bash->Read が最頻（2 回）
        if not r["top_chains"] or r["top_chains"][0][0] != "Bash -> Read":
            fails.append("top chain expected 'Bash -> Read', got {}".format(r["top_chains"]))
        if r["top_chains"][0][1] != 2:
            fails.append("top chain count expected 2, got {}".format(r["top_chains"][0][1]))
        # (b) 源欠如
        empty = run_tool_pattern({"log_dir": os.path.join(d, "nope")})
        if empty["source_ok"] is not False:
            fails.append("missing log-dir should be source_ok False")
        if "観測継続" not in render_tool_pattern(empty):
            fails.append("missing render should say 観測継続")
    finally:
        _rmtree(d)
    return fails


# ==========================================================================
# 2. retry
# ==========================================================================
def _is_failure_exit(code):
    """exit_code が非空かつ '0' 以外なら失敗（空=データ無なので失敗にしない）。"""
    if code is None:
        return False
    s = str(code).strip()
    if s == "":
        return False
    return s != "0"


def run_retry(opts):
    log_dir = opts["log_dir"]
    events = load_tool_events(log_dir)
    r = {"sub": "retry", "log_dir": log_dir, "source_ok": bool(events),
         "total_events": len(events), "total_failures": 0, "by_tool": {},
         "with_exit_code": 0}
    if not events:
        return r
    by_tool = {}
    for e in events:
        code = e.get("exit_code", "")
        if str(code).strip() != "":
            r["with_exit_code"] += 1
        if _is_failure_exit(code):
            tool = e.get("tool") or "unknown"
            by_tool[tool] = by_tool.get(tool, 0) + 1
    r["by_tool"] = by_tool
    r["total_failures"] = sum(by_tool.values())
    r["source_ok"] = True
    return r


def render_retry(r):
    out = ["retry — {}".format(r["log_dir"])]
    if not r["source_ok"]:
        out.append(_continue_line("tools.jsonl 未生成"))
        return "\n".join(out)
    out.append("events: {} / exit_code 記録あり: {} / 失敗(非0): {}".format(
        r["total_events"], r["with_exit_code"], r["total_failures"]))
    if r["total_failures"] == 0:
        out.append("失敗/リトライ検出なし（exit_code 非空非0 が 0 件）。")
        return "\n".join(out)
    out.append("")
    out.append("failures by tool:")
    for tool, n in _sorted_desc(r["by_tool"]):
        out.append("  {:>5}  {}".format(n, tool))
    return "\n".join(out)


def selftest_retry():
    fails = []
    d = tempfile.mkdtemp(prefix="agentlog-retry-")
    try:
        _write_jsonl(os.path.join(d, "2026-07-04-tools.jsonl"), [
            {"ts": "t", "tool": "Bash", "matcher": "", "exit_code": "1"},
            {"ts": "t", "tool": "Bash", "matcher": "", "exit_code": "0"},
            {"ts": "t", "tool": "Edit", "matcher": "", "exit_code": "2"},
            {"ts": "t", "tool": "Read", "matcher": "", "exit_code": ""},
        ])
        r = run_retry({"log_dir": d})
        if r["total_failures"] != 2:
            fails.append("failures expected 2, got {}".format(r["total_failures"]))
        if r["by_tool"] != {"Bash": 1, "Edit": 1}:
            fails.append("by_tool mismatch: {}".format(r["by_tool"]))
        if r["with_exit_code"] != 3:
            fails.append("with_exit_code expected 3, got {}".format(r["with_exit_code"]))
        empty = run_retry({"log_dir": os.path.join(d, "nope")})
        if empty["source_ok"] is not False:
            fails.append("missing should be source_ok False")
        if "観測継続" not in render_retry(empty):
            fails.append("missing render should say 観測継続")
    finally:
        _rmtree(d)
    return fails


# ==========================================================================
# 3. cost
# ==========================================================================
def _ts_date(ts):
    if not isinstance(ts, str) or len(ts) < 10:
        return "unknown"
    return ts[:10]


def run_cost(opts):
    log_dir = opts["log_dir"]
    events = load_tool_events(log_dir)
    r = {"sub": "cost", "log_dir": log_dir, "source_ok": bool(events),
         "total": len(events), "by_day": {}, "peak_day": None, "peak_count": 0}
    if not events:
        return r
    by_day = {}
    for e in events:
        d = _ts_date(e.get("ts"))
        by_day[d] = by_day.get(d, 0) + 1
    r["by_day"] = by_day
    top = _sorted_desc(by_day)[0]
    r["peak_day"], r["peak_count"] = top[0], top[1]
    r["source_ok"] = True
    return r


def render_cost(r):
    out = ["cost — {}".format(r["log_dir"])]
    if not r["source_ok"]:
        out.append(_continue_line("tools.jsonl 未生成"))
        return "\n".join(out)
    out.append("total calls: {} / days: {}".format(r["total"], len(r["by_day"])))
    out.append("peak day (コスト偏重): {} = {} calls".format(r["peak_day"], r["peak_count"]))
    out.append("")
    out.append("by day:")
    for d, n in sorted(r["by_day"].items()):
        out.append("  {}  {:>5}".format(d, n))
    return "\n".join(out)


def selftest_cost():
    fails = []
    d = tempfile.mkdtemp(prefix="agentlog-cost-")
    try:
        _write_jsonl(os.path.join(d, "2026-07-03-tools.jsonl"), [
            {"ts": "2026-07-03T09:00:00+0900", "tool": "Bash", "exit_code": ""},
        ])
        _write_jsonl(os.path.join(d, "2026-07-04-tools.jsonl"), [
            {"ts": "2026-07-04T09:00:00+0900", "tool": "Bash", "exit_code": ""},
            {"ts": "2026-07-04T09:01:00+0900", "tool": "Read", "exit_code": ""},
            {"ts": "2026-07-04T09:02:00+0900", "tool": "Edit", "exit_code": ""},
        ])
        r = run_cost({"log_dir": d})
        if r["by_day"] != {"2026-07-03": 1, "2026-07-04": 3}:
            fails.append("by_day mismatch: {}".format(r["by_day"]))
        if r["peak_day"] != "2026-07-04" or r["peak_count"] != 3:
            fails.append("peak mismatch: {} {}".format(r["peak_day"], r["peak_count"]))
        empty = run_cost({"log_dir": os.path.join(d, "nope")})
        if empty["source_ok"] is not False:
            fails.append("missing should be source_ok False")
        if "観測継続" not in render_cost(empty):
            fails.append("missing render should say 観測継続")
    finally:
        _rmtree(d)
    return fails


# ==========================================================================
# 4. denials
# ==========================================================================
def _transcript_denials(paths):
    """transcript の tool_result で is_error かつ denied/permission/blocked を数える。"""
    count = 0
    samples = []
    for p in paths:
        for rec in load_transcript(p):
            for b in msg_blocks(rec):
                if b.get("type") != "tool_result":
                    continue
                if not b.get("is_error"):
                    continue
                c = b.get("content")
                s = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False, default=str)
                low = s.lower()
                if ("denied" in low or "permission for this action" in low
                        or "blocked" in low or "拒否" in low):
                    count += 1
                    if len(samples) < 5:
                        samples.append(s.strip().replace("\n", " ")[:120])
    return count, samples


def run_denials(opts):
    log_dir = opts["log_dir"]
    events = load_tool_events(log_dir)
    paths = transcript_paths(opts)
    r = {"sub": "denials", "log_dir": log_dir,
         "tools_source_ok": bool(events), "transcript_source_ok": bool(paths),
         "tools_denials": 0, "tools_by_tool": {},
         "transcript_denials": 0, "transcript_samples": [], "total": 0}
    by_tool = {}
    for e in events:
        if (e.get("tool") in ("Bash", "Edit")) and _is_failure_exit(e.get("exit_code")):
            t = e.get("tool")
            by_tool[t] = by_tool.get(t, 0) + 1
    r["tools_by_tool"] = by_tool
    r["tools_denials"] = sum(by_tool.values())
    tc, samples = _transcript_denials(paths)
    r["transcript_denials"] = tc
    r["transcript_samples"] = samples
    r["total"] = r["tools_denials"] + r["transcript_denials"]
    return r


def render_denials(r):
    out = ["denials — logs={} transcript={}".format(
        "ok" if r["tools_source_ok"] else "none",
        "ok" if r["transcript_source_ok"] else "none")]
    if not r["tools_source_ok"] and not r["transcript_source_ok"]:
        out.append(_continue_line("tools.jsonl と transcript の双方が未発見"))
        return "\n".join(out)
    out.append("total denials(推定): {}".format(r["total"]))
    out.append("  tools.jsonl 異常 exit (Bash/Edit): {}".format(r["tools_denials"]))
    for t, n in _sorted_desc(r["tools_by_tool"]):
        out.append("    {:>4}  {}".format(n, t))
    out.append("  transcript denied/BLOCKED: {}".format(r["transcript_denials"]))
    for s in r["transcript_samples"]:
        out.append("    - {}".format(s))
    return "\n".join(out)


def selftest_denials():
    fails = []
    d = tempfile.mkdtemp(prefix="agentlog-den-")
    try:
        _write_jsonl(os.path.join(d, "2026-07-04-tools.jsonl"), [
            {"ts": "t", "tool": "Bash", "exit_code": "127"},
            {"ts": "t", "tool": "Edit", "exit_code": "1"},
            {"ts": "t", "tool": "Bash", "exit_code": "0"},
            {"ts": "t", "tool": "Read", "exit_code": "1"},  # Read は対象外
        ])
        tp = os.path.join(d, "t.jsonl")
        _write_jsonl(tp, [
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "is_error": True,
                 "content": "Permission for this action was denied by the classifier."}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "is_error": False, "content": "ok"}]}},
        ])
        r = run_denials({"log_dir": d, "transcript": tp})
        if r["tools_denials"] != 2:
            fails.append("tools_denials expected 2, got {}".format(r["tools_denials"]))
        if r["tools_by_tool"] != {"Bash": 1, "Edit": 1}:
            fails.append("tools_by_tool mismatch: {}".format(r["tools_by_tool"]))
        if r["transcript_denials"] != 1:
            fails.append("transcript_denials expected 1, got {}".format(r["transcript_denials"]))
        if r["total"] != 3:
            fails.append("total expected 3, got {}".format(r["total"]))
        # (b) 双方源欠如
        empty = run_denials({"log_dir": os.path.join(d, "nope"),
                             "transcript": os.path.join(d, "nope.jsonl")})
        if empty["tools_source_ok"] or empty["transcript_source_ok"]:
            fails.append("missing both should be source_ok False")
        if "観測継続" not in render_denials(empty):
            fails.append("missing render should say 観測継続")
    finally:
        _rmtree(d)
    return fails


# ==========================================================================
# 5. subagent
# ==========================================================================
def _analyze_subagents_file(recs):
    """1 transcript の Agent 起動と（sidechain があれば）子 tool_use を数える。"""
    has_sidechain = any(rec.get("isSidechain") for rec in recs)
    agents = []  # {subagent_type, description, child_tool_uses}
    current = None
    for rec in recs:
        side = bool(rec.get("isSidechain"))
        for b in msg_blocks(rec):
            if b.get("type") == "tool_use" and b.get("name") == "Agent" and not side:
                inp = b.get("input", {}) if isinstance(b.get("input"), dict) else {}
                current = {"subagent_type": inp.get("subagent_type", "?"),
                           "description": (inp.get("description") or "")[:60],
                           "child_tool_uses": 0}
                agents.append(current)
            elif b.get("type") == "tool_use" and side and current is not None:
                current["child_tool_uses"] += 1
    return has_sidechain, agents


def run_subagent(opts):
    paths = transcript_paths(opts)
    r = {"sub": "subagent", "source_ok": bool(paths), "files": len(paths),
         "total_agents": 0, "by_type": {}, "child_data_available": False,
         "over_launch": [], "over_launch_count": None}
    if not paths:
        return r
    all_agents = []
    child_available = False
    for p in paths:
        has_side, agents = _analyze_subagents_file(load_transcript(p))
        if has_side:
            child_available = True
        all_agents.extend([(has_side, a) for a in agents])
    by_type = {}
    for _, a in all_agents:
        t = a["subagent_type"]
        by_type[t] = by_type.get(t, 0) + 1
    r["total_agents"] = len(all_agents)
    r["by_type"] = by_type
    r["child_data_available"] = child_available
    if child_available:
        over = [a for has_side, a in all_agents if has_side and a["child_tool_uses"] <= 2]
        r["over_launch"] = over
        r["over_launch_count"] = len(over)
    r["source_ok"] = True
    return r


def render_subagent(r):
    out = ["subagent — {} file".format(r["files"]) if r["source_ok"] else "subagent"]
    if not r["source_ok"]:
        out.append(_continue_line("transcript 未発見"))
        return "\n".join(out)
    out.append("Agent 起動: {}".format(r["total_agents"]))
    for t, n in _sorted_desc(r["by_type"]):
        out.append("  {:>4}  {}".format(n, t))
    if not r["child_data_available"]:
        out.append("子 tool_use: transcript に sidechain 記録なし — 過剰起動 heuristic 適用不可（観測継続）。")
    else:
        out.append("過剰起動疑い (子 tool_use<=2): {}".format(r["over_launch_count"]))
        for a in r["over_launch"][:10]:
            out.append("  - {} (child={}) {}".format(
                a["subagent_type"], a["child_tool_uses"], a["description"]))
    return "\n".join(out)


def selftest_subagent():
    fails = []
    d = tempfile.mkdtemp(prefix="agentlog-sub-")
    try:
        au = "agent-uuid-1"
        tp = os.path.join(d, "t.jsonl")
        _write_jsonl(tp, [
            # Agent 起動 A（過小: 子 tool_use 1）
            {"type": "assistant", "uuid": au, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Agent",
                 "input": {"subagent_type": "Explore", "description": "tiny grep"}}]}},
            {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Grep", "input": {}}]}},
            # Agent 起動 B（正常: 子 tool_use 3）
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Agent",
                 "input": {"subagent_type": "Plan", "description": "design"}}]}},
            {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Read", "input": {}},
                {"type": "tool_use", "name": "Read", "input": {}}]}},
            {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash", "input": {}}]}},
        ])
        r = run_subagent({"transcript": tp})
        if r["total_agents"] != 2:
            fails.append("total_agents expected 2, got {}".format(r["total_agents"]))
        if r["by_type"] != {"Explore": 1, "Plan": 1}:
            fails.append("by_type mismatch: {}".format(r["by_type"]))
        if not r["child_data_available"]:
            fails.append("child_data_available expected True (fixture has sidechain)")
        if r["over_launch_count"] != 1:
            fails.append("over_launch_count expected 1, got {}".format(r["over_launch_count"]))
        if r["over_launch"] and r["over_launch"][0]["subagent_type"] != "Explore":
            fails.append("over_launch should be Explore, got {}".format(r["over_launch"]))
        # sidechain 無し transcript → child 集計は unavailable
        tp2 = os.path.join(d, "t2.jsonl")
        _write_jsonl(tp2, [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Agent",
                 "input": {"subagent_type": "Explore", "description": "x"}}]}},
        ])
        r2 = run_subagent({"transcript": tp2})
        if r2["total_agents"] != 1:
            fails.append("no-sidechain total_agents expected 1, got {}".format(r2["total_agents"]))
        if r2["child_data_available"]:
            fails.append("no-sidechain child_data_available expected False")
        if r2["over_launch_count"] is not None:
            fails.append("no-sidechain over_launch_count expected None")
        # (b) 源欠如
        empty = run_subagent({"transcript": os.path.join(d, "nope.jsonl")})
        if empty["source_ok"] is not False:
            fails.append("missing should be source_ok False")
        if "観測継続" not in render_subagent(empty):
            fails.append("missing render should say 観測継続")
    finally:
        _rmtree(d)
    return fails


# ==========================================================================
# 6. context-bloat
# ==========================================================================
def _session_of(rec):
    return rec.get("sessionId") or "unknown-session"


def _collect_sessions(paths):
    """session 別に messages / approx chars / compacts を集計。"""
    sess = {}
    for p in paths:
        for rec in load_transcript(p):
            t = rec.get("type")
            sid = _session_of(rec)
            s = sess.setdefault(sid, {"messages": 0, "chars": 0, "compacts": 0})
            if t in ("user", "assistant"):
                s["messages"] += 1
                for b in msg_blocks(rec):
                    s["chars"] += approx_block_chars(b)
            if (t == "system" and rec.get("subtype") == "compact_boundary") or rec.get("isCompactSummary"):
                s["compacts"] += 1
    return sess


def run_context_bloat(opts):
    paths = transcript_paths(opts)
    r = {"sub": "context-bloat", "source_ok": bool(paths), "sessions": 0,
         "bloated": [], "all_sessions": []}
    if not paths:
        return r
    sess = _collect_sessions(paths)
    rows = []
    for sid, s in sess.items():
        bloated = s["messages"] >= BLOAT_MSG_THRESHOLD or s["chars"] >= BLOAT_CHAR_THRESHOLD
        rows.append({"session": sid, "messages": s["messages"], "chars": s["chars"],
                     "bloated": bloated})
    rows.sort(key=lambda x: (-x["chars"], -x["messages"], x["session"]))
    r["sessions"] = len(rows)
    r["all_sessions"] = rows
    r["bloated"] = [x for x in rows if x["bloated"]]
    r["source_ok"] = True
    return r


def render_context_bloat(r):
    out = ["context-bloat — thresholds msgs>={} chars>={}".format(
        BLOAT_MSG_THRESHOLD, BLOAT_CHAR_THRESHOLD)]
    if not r["source_ok"]:
        out.append(_continue_line("transcript 未発見"))
        return "\n".join(out)
    out.append("sessions: {} / bloated: {}".format(r["sessions"], len(r["bloated"])))
    out.append("")
    out.append("top sessions (by approx chars):")
    for x in r["all_sessions"][:10]:
        flag = " *BLOAT*" if x["bloated"] else ""
        out.append("  msgs={:>4} chars={:>8}  {}{}".format(
            x["messages"], x["chars"], x["session"], flag))
    return "\n".join(out)


def selftest_context_bloat():
    fails = []
    d = tempfile.mkdtemp(prefix="agentlog-cb-")
    try:
        tp = os.path.join(d, "t.jsonl")
        big_text = "x" * 500
        rows = []
        # 肥大 session: 250 メッセージ
        for i in range(250):
            rows.append({"type": "assistant", "sessionId": "big",
                         "message": {"role": "assistant", "content": [
                             {"type": "text", "text": big_text}]}})
        # 小 session: 3 メッセージ
        for i in range(3):
            rows.append({"type": "user", "sessionId": "small",
                         "message": {"role": "user", "content": "hi"}})
        _write_jsonl(tp, rows)
        r = run_context_bloat({"transcript": tp})
        if r["sessions"] != 2:
            fails.append("sessions expected 2, got {}".format(r["sessions"]))
        bloated_ids = [x["session"] for x in r["bloated"]]
        if bloated_ids != ["big"]:
            fails.append("bloated expected ['big'], got {}".format(bloated_ids))
        # big が chars 降順で先頭
        if r["all_sessions"][0]["session"] != "big":
            fails.append("top session expected 'big', got {}".format(r["all_sessions"][0]["session"]))
        empty = run_context_bloat({"transcript": os.path.join(d, "nope.jsonl")})
        if empty["source_ok"] is not False:
            fails.append("missing should be source_ok False")
        if "観測継続" not in render_context_bloat(empty):
            fails.append("missing render should say 観測継続")
    finally:
        _rmtree(d)
    return fails


# ==========================================================================
# 7. session-hygiene
# ==========================================================================
def run_session_hygiene(opts):
    paths = transcript_paths(opts)
    r = {"sub": "session-hygiene", "source_ok": bool(paths), "sessions": 0, "rows": []}
    if not paths:
        return r
    sess = _collect_sessions(paths)
    rows = [{"session": sid, "messages": s["messages"], "compacts": s["compacts"]}
            for sid, s in sess.items()]
    rows.sort(key=lambda x: (-x["messages"], x["session"]))
    r["sessions"] = len(rows)
    r["rows"] = rows
    r["source_ok"] = True
    return r


def render_session_hygiene(r):
    out = ["session-hygiene"]
    if not r["source_ok"]:
        out.append(_continue_line("transcript 未発見"))
        return "\n".join(out)
    out.append("sessions: {}".format(r["sessions"]))
    out.append("")
    out.append("by session (messages / compacts):")
    for x in r["rows"][:15]:
        out.append("  msgs={:>4} compacts={:>2}  {}".format(
            x["messages"], x["compacts"], x["session"]))
    return "\n".join(out)


def selftest_session_hygiene():
    fails = []
    d = tempfile.mkdtemp(prefix="agentlog-sh-")
    try:
        tp = os.path.join(d, "t.jsonl")
        _write_jsonl(tp, [
            {"type": "user", "sessionId": "s1", "message": {"role": "user", "content": "a"}},
            {"type": "assistant", "sessionId": "s1", "message": {"role": "assistant", "content": "b"}},
            {"type": "system", "sessionId": "s1", "subtype": "compact_boundary"},
            {"type": "assistant", "sessionId": "s1", "isCompactSummary": True,
             "message": {"role": "assistant", "content": "summary"}},
            {"type": "user", "sessionId": "s2", "message": {"role": "user", "content": "c"}},
        ])
        r = run_session_hygiene({"transcript": tp})
        if r["sessions"] != 2:
            fails.append("sessions expected 2, got {}".format(r["sessions"]))
        s1 = next(x for x in r["rows"] if x["session"] == "s1")
        # s1: user + assistant + isCompactSummary(assistant) = 3 messages, compacts = 2
        if s1["messages"] != 3:
            fails.append("s1 messages expected 3, got {}".format(s1["messages"]))
        if s1["compacts"] != 2:
            fails.append("s1 compacts expected 2, got {}".format(s1["compacts"]))
        empty = run_session_hygiene({"transcript": os.path.join(d, "nope.jsonl")})
        if empty["source_ok"] is not False:
            fails.append("missing should be source_ok False")
        if "観測継続" not in render_session_hygiene(empty):
            fails.append("missing render should say 観測継続")
    finally:
        _rmtree(d)
    return fails


# ==========================================================================
# 8. unverified-claims
# ==========================================================================
def _analyze_unverified_file(recs):
    """状態断定句の直前 turn に live 実測 tool 呼出が無い箇所を検出。"""
    flagged = []
    live_seen = False  # 直近の user プロンプト以降に live 実測があったか
    for rec in recs:
        t = rec.get("type")
        blocks = msg_blocks(rec)
        if t == "user":
            # tool_result のみの user は turn 境界にしない（実際の人間発話でリセット）
            is_tool_result = blocks and all(b.get("type") == "tool_result" for b in blocks)
            if not is_tool_result:
                live_seen = False
            continue
        if t != "assistant":
            continue
        for b in blocks:
            if b.get("type") == "tool_use":
                name = b.get("name", "")
                inp = b.get("input", {}) if isinstance(b.get("input"), dict) else {}
                if name == "Bash" and is_live_bash(inp.get("command", "")):
                    live_seen = True
                elif name.startswith("mcp__plugin_github") or name.startswith("mcp__github"):
                    live_seen = True
            elif b.get("type") == "text":
                txt = block_text(b)
                phrase = contains_claim(txt)
                if phrase and not live_seen:
                    flagged.append({"phrase": phrase,
                                    "snippet": txt.strip().replace("\n", " ")[:100]})
    return flagged


def run_unverified_claims(opts):
    paths = transcript_paths(opts)
    r = {"sub": "unverified-claims", "source_ok": bool(paths), "files": len(paths),
         "flagged_count": 0, "flagged": []}
    if not paths:
        return r
    flagged = []
    for p in paths:
        flagged.extend(_analyze_unverified_file(load_transcript(p)))
    r["flagged"] = flagged
    r["flagged_count"] = len(flagged)
    r["source_ok"] = True
    return r


def render_unverified_claims(r):
    out = ["unverified-claims — {} file".format(r["files"]) if r["source_ok"] else "unverified-claims"]
    if not r["source_ok"]:
        out.append(_continue_line("transcript 未発見"))
        return "\n".join(out)
    out.append("状態断定句で直前 turn に live 実測(gh/curl/aws/git)無し: {}".format(r["flagged_count"]))
    for f in r["flagged"][:15]:
        out.append("  [{}] {}".format(f["phrase"], f["snippet"]))
    return "\n".join(out)


def selftest_unverified_claims():
    fails = []
    d = tempfile.mkdtemp(prefix="agentlog-uc-")
    try:
        tp = os.path.join(d, "t.jsonl")
        _write_jsonl(tp, [
            # turn 1: gh 実測 → 断定は OK（flag しない）
            {"type": "user", "message": {"role": "user", "content": "状況は?"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash",
                 "input": {"command": "gh pr view 5 --json state"}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "content": "MERGED"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "PR はマージ済みです。"}]}},
            # turn 2: 実測なしで断定 → flag する
            {"type": "user", "message": {"role": "user", "content": "次は?"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "本番へデプロイ完了しました。"}]}},
        ])
        r = run_unverified_claims({"transcript": tp})
        if r["flagged_count"] != 1:
            fails.append("flagged expected 1, got {}: {}".format(r["flagged_count"], r["flagged"]))
        if r["flagged"] and "デプロイ" not in r["flagged"][0]["snippet"]:
            fails.append("flagged snippet mismatch: {}".format(r["flagged"]))
        empty = run_unverified_claims({"transcript": os.path.join(d, "nope.jsonl")})
        if empty["source_ok"] is not False:
            fails.append("missing should be source_ok False")
        if "観測継続" not in render_unverified_claims(empty):
            fails.append("missing render should say 観測継続")
    finally:
        _rmtree(d)
    return fails


# ==========================================================================
# 9. rework
# ==========================================================================
def collect_git_commits(root, days):
    """git log を name-only で収集。repo でなければ None（源欠如）。"""
    if not is_git_repo(root):
        return None
    rc, out, _ = _run_git(root, [
        "log", "--since={} days ago".format(days), "--name-only",
        "--date=short", "--pretty=format:%x1e%H%x1f%ad%x1f%s"])
    if rc != 0:
        return None
    commits = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        lines = chunk.split("\n")
        head = lines[0].split("\x1f")
        if len(head) < 3:
            continue
        files = [x.strip() for x in lines[1:] if x.strip()]
        commits.append({"hash": head[0], "date": head[1], "subject": head[2], "files": files})
    return commits


def rework_analyze(commits, days):
    """revert / 同一ファイル再編集（手戻り）を集計。"""
    r = {"sub": "rework", "source_ok": True, "window_days": days,
         "commits": len(commits), "reverts": [], "rework_files": []}
    file_counts = {}
    for c in commits:
        subj = c.get("subject", "")
        if subj.startswith("Revert") or subj.lower().startswith("revert:") or "ロールバック" in subj:
            r["reverts"].append({"hash": c.get("hash", "")[:8], "subject": subj[:80]})
        for f in c.get("files", []):
            file_counts[f] = file_counts.get(f, 0) + 1
    r["rework_files"] = [{"file": f, "edits": n}
                         for f, n in _sorted_desc(file_counts) if n >= 2]
    return r


def run_rework(opts):
    commits = collect_git_commits(opts["root"], opts["days"])
    if commits is None:
        return {"sub": "rework", "source_ok": False, "window_days": opts["days"],
                "root": opts["root"]}
    r = rework_analyze(commits, opts["days"])
    r["root"] = opts["root"]
    return r


def render_rework(r):
    out = ["rework — {} (直近{}日)".format(r.get("root", ""), r["window_days"])]
    if not r["source_ok"]:
        out.append(_continue_line("git repo でない / git log 取得不可"))
        return "\n".join(out)
    out.append("commits: {} / revert: {} / 手戻りファイル(>=2 編集): {}".format(
        r["commits"], len(r["reverts"]), len(r["rework_files"])))
    if r["reverts"]:
        out.append("")
        out.append("reverts:")
        for rv in r["reverts"][:10]:
            out.append("  {} {}".format(rv["hash"], rv["subject"]))
    if r["rework_files"]:
        out.append("")
        out.append("most re-edited files:")
        for f in r["rework_files"][:10]:
            out.append("  {:>3}x  {}".format(f["edits"], f["file"]))
    return "\n".join(out)


def selftest_rework():
    fails = []
    # (a) 合成コミットで集計正当性（git 不要）
    commits = [
        {"hash": "aaaa1111", "date": "2026-07-01", "subject": "feat: add x",
         "files": ["a.py", "b.py"]},
        {"hash": "bbbb2222", "date": "2026-07-02", "subject": "fix: patch x",
         "files": ["a.py"]},
        {"hash": "cccc3333", "date": "2026-07-03", "subject": "Revert \"feat: add x\"",
         "files": ["a.py", "c.py"]},
    ]
    r = rework_analyze(commits, 30)
    if len(r["reverts"]) != 1:
        fails.append("reverts expected 1, got {}".format(len(r["reverts"])))
    rf = {x["file"]: x["edits"] for x in r["rework_files"]}
    if rf != {"a.py": 3}:
        fails.append("rework_files expected a.py=3, got {}".format(rf))
    # (b) 源欠如: 非 repo temp dir → 観測継続
    d = tempfile.mkdtemp(prefix="agentlog-rework-")
    try:
        empty = run_rework({"root": d, "days": 30})
        if empty["source_ok"] is not False:
            fails.append("non-repo should be source_ok False")
        if "観測継続" not in render_rework(empty):
            fails.append("non-repo render should say 観測継続")
    finally:
        _rmtree(d)
    return fails


# ==========================================================================
# 10. mistake-tags
# ==========================================================================
MISTAKE_TAG_RULES = [
    ("unverified-state", ["文書ベース", "実測", "自己申告", "自己判定", "状態推定",
                          "推測", "live", "near-miss", "0 件", "ゼロ件"]),
    ("shell-compat", ["grep -p", "zsh", "クォート", "単語分割", "bsd", "エスケープ",
                      "shebang", "for x in", "set --"]),
    ("dependency-fragility", ["jq", "spof", "fail-closed", "フォールバック", "依存 cli",
                              "単一 cli"]),
    ("config-schema", ["settings.json", "無効キー", "schema", "enum", "invalid key"]),
    ("git-hygiene", ["force push", "reset --hard", "stage", "同乗", "ブランチ削除",
                     "main 直接", "merged ≠", "concurrency"]),
    ("deploy-verification", ["amplify", "deploy", "デプロイ", "本番", "反映", "autobuild"]),
    ("over-engineering", ["重複", "過剰", "冗長", "shadow", "要求外", "over-fire"]),
    ("context-bloat", ["200 行", "常駐", "肥大", "キャッシュ", "context diet"]),
]


def _parse_mistakes(text):
    """`### YYYY-MM-DD: ...` の見出しごとに (heading, body) を切り出す。"""
    lines = text.splitlines()
    mistakes = []
    cur_head = None
    cur_body = []
    head_re = re.compile(r"^###\s+\d{4}-\d{2}-\d{2}\s*[:：]")
    for line in lines:
        if head_re.match(line):
            if cur_head is not None:
                mistakes.append((cur_head, "\n".join(cur_body)))
            cur_head = line.strip()
            cur_body = []
        elif cur_head is not None:
            if line.startswith("## ") or line.startswith("### "):
                # 別セクション開始 → 現ミス確定
                mistakes.append((cur_head, "\n".join(cur_body)))
                cur_head = None
                cur_body = []
            else:
                cur_body.append(line)
    if cur_head is not None:
        mistakes.append((cur_head, "\n".join(cur_body)))
    return mistakes


def _tag_mistake(heading, body):
    blob = (heading + "\n" + body).lower()
    tags = []
    for tag, kws in MISTAKE_TAG_RULES:
        if any(kw.lower() in blob for kw in kws):
            tags.append(tag)
    if not tags:
        tags.append("other")
    return tags


def mistake_tags_analyze(text):
    mistakes = _parse_mistakes(text)
    by_tag = {}
    for head, body in mistakes:
        for tag in _tag_mistake(head, body):
            by_tag[tag] = by_tag.get(tag, 0) + 1
    return {"sub": "mistake-tags", "source_ok": True, "mistakes": len(mistakes),
            "by_tag": by_tag, "top": _sorted_desc(by_tag)}


def run_mistake_tags(opts):
    path = os.path.join(opts["root"], "assets/knowledge/mistake-log-archive.md")
    if not os.path.exists(path):
        return {"sub": "mistake-tags", "source_ok": False, "path": path}
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    r = mistake_tags_analyze(text)
    r["path"] = path
    return r


def render_mistake_tags(r):
    out = ["mistake-tags — {}".format(r.get("path", ""))]
    if not r["source_ok"]:
        out.append(_continue_line("mistake-log-archive.md 未発見"))
        return "\n".join(out)
    out.append("mistakes parsed: {}".format(r["mistakes"]))
    if r["mistakes"] == 0:
        out.append("時系列ミス見出し (### YYYY-MM-DD:) が 0 件（観測継続）。")
        return "\n".join(out)
    out.append("")
    out.append("再発 top (根本原因タグ):")
    for tag, n in r["top"]:
        out.append("  {:>4}  {}".format(n, tag))
    return "\n".join(out)


def selftest_mistake_tags():
    fails = []
    text = "\n".join([
        "# ミスログ",
        "## ミスログ（時系列）",
        "### 2026-04-10: settings.json に無効キー",
        "- 何が起きたか: schema enum 外の invalid key を記述",
        "",
        "### 2026-06-14: zsh の単語分割で for ループ誤動作",
        "- 原因: 未クォート $var が単語分割されない zsh 挙動",
        "",
        "### 2026-07-03: 自己判定で達成を誤宣言（文書ベース状態推定）",
        "- 原因: live 実測せず自己申告で完了と誤断定",
        "",
    ])
    r = mistake_tags_analyze(text)
    if r["mistakes"] != 3:
        fails.append("mistakes expected 3, got {}".format(r["mistakes"]))
    if r["by_tag"].get("config-schema") != 1:
        fails.append("config-schema expected 1, got {}".format(r["by_tag"].get("config-schema")))
    if r["by_tag"].get("shell-compat") != 1:
        fails.append("shell-compat expected 1, got {}".format(r["by_tag"].get("shell-compat")))
    if r["by_tag"].get("unverified-state") != 1:
        fails.append("unverified-state expected 1, got {}".format(r["by_tag"].get("unverified-state")))
    # (b) 源欠如
    d = tempfile.mkdtemp(prefix="agentlog-mt-")
    try:
        empty = run_mistake_tags({"root": d})
        if empty["source_ok"] is not False:
            fails.append("missing should be source_ok False")
        if "観測継続" not in render_mistake_tags(empty):
            fails.append("missing render should say 観測継続")
    finally:
        _rmtree(d)
    return fails


# ==========================================================================
# 11. routine-drift
# ==========================================================================
def load_registry_rows(root):
    """registry.md の SWEEP-TABLE 行 (id, cadence, desc) を返す。無ければ None。"""
    # 定期ルーチンの登録簿。配置はリポジトリごとに異なるため env で差し替え可。
    rel = os.environ.get("PH_ROUTINE_REGISTRY", "ops/routines/registry.md")
    path = os.path.join(root, rel)
    if not os.path.exists(path):
        return None
    rows = []
    in_table = False
    row_re = re.compile(r"^\|\s*([a-z0-9][a-z0-9-]*)\s*\|\s*([a-z-]+)\s*\|(.*)\|\s*$")
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if "SWEEP-TABLE:START" in line:
                in_table = True
                continue
            if "SWEEP-TABLE:END" in line:
                break
            if not in_table:
                continue
            m = row_re.match(line)
            if not m:
                continue
            rid, cadence, desc = m.group(1), m.group(2), m.group(3).strip()
            if rid == "id":
                continue
            rows.append((rid, cadence, desc))
    return rows


def collect_routine_commits(root, ids, days):
    """各 routine id を subject に含む最新 commit 日付を返す。repo でなければ None。"""
    if not is_git_repo(root):
        return None
    rc, out, _ = _run_git(root, [
        "log", "--since={} days ago".format(days), "--date=short",
        "--pretty=format:%ad\x1f%s"])
    if rc != 0:
        return None
    last = {}
    for line in out.splitlines():
        parts = line.split("\x1f", 1)
        if len(parts) != 2:
            continue
        d, subj = parts[0].strip(), parts[1]
        for rid in ids:
            if rid in subj:
                cur = last.get(rid)
                if cur is None or d > cur:
                    last[rid] = d
    return last


def routine_drift_analyze(rows, last_dates, today):
    results = []
    for rid, cadence, desc in rows:
        sla = ROUTINE_SLA_DAYS.get(cadence, 9)
        last = last_dates.get(rid)
        if last is None:
            results.append({"id": rid, "cadence": cadence, "last": None,
                            "age_days": None, "sla_days": sla, "status": "no-commit"})
            continue
        try:
            age = (today - date.fromisoformat(last)).days
        except ValueError:
            age = None
        status = "ok"
        if age is None:
            status = "unknown-date"
        elif age > sla:
            status = "drift"
        results.append({"id": rid, "cadence": cadence, "last": last,
                        "age_days": age, "sla_days": sla, "status": status})
    return {"sub": "routine-drift", "source_ok": True, "today": today.isoformat(),
            "routines": len(rows), "results": results,
            "drift": [x for x in results if x["status"] in ("drift", "no-commit")]}


def run_routine_drift(opts):
    rows = load_registry_rows(opts["root"])
    if rows is None:
        return {"sub": "routine-drift", "source_ok": False, "reason": "registry 未発見"}
    ids = [r[0] for r in rows]
    last = collect_routine_commits(opts["root"], ids, max(opts["days"], 90))
    if last is None:
        return {"sub": "routine-drift", "source_ok": False, "reason": "git repo でない"}
    r = routine_drift_analyze(rows, last, today_jst())
    return r


def render_routine_drift(r):
    out = ["routine-drift"]
    if not r["source_ok"]:
        out.append(_continue_line(r.get("reason", "源欠如")))
        return "\n".join(out)
    out.append("routines: {} / drift or no-commit: {} (today={})".format(
        r["routines"], len(r["drift"]), r["today"]))
    out.append("")
    for x in r["results"]:
        mark = {"ok": "  ", "drift": "!!", "no-commit": "??", "unknown-date": "? "}.get(x["status"], "  ")
        out.append("  {} {:<28} {:<11} last={} age={} sla={}".format(
            mark, x["id"], x["cadence"], x["last"], x["age_days"], x["sla_days"]))
    return "\n".join(out)


def selftest_routine_drift():
    fails = []
    rows = [
        ("nightly-triage", "daily", "..."),
        ("wp-smoke-check", "weekly-fri", "..."),
        ("harness-review", "monthly", "..."),
        ("never-run-routine", "daily", "..."),
    ]
    today = date(2026, 7, 4)
    last_dates = {
        "nightly-triage": "2026-07-04",   # 当日 → ok
        "wp-smoke-check": "2026-06-20",   # 14 日前 > sla 9 → drift
        "harness-review": "2026-06-15",   # 19 日前 < sla 35 → ok
        # never-run-routine: 無し → no-commit
    }
    r = routine_drift_analyze(rows, last_dates, today)
    status = {x["id"]: x["status"] for x in r["results"]}
    if status.get("nightly-triage") != "ok":
        fails.append("nightly-triage expected ok, got {}".format(status.get("nightly-triage")))
    if status.get("wp-smoke-check") != "drift":
        fails.append("wp-smoke-check expected drift, got {}".format(status.get("wp-smoke-check")))
    if status.get("harness-review") != "ok":
        fails.append("harness-review expected ok, got {}".format(status.get("harness-review")))
    if status.get("never-run-routine") != "no-commit":
        fails.append("never-run-routine expected no-commit, got {}".format(status.get("never-run-routine")))
    if len(r["drift"]) != 2:
        fails.append("drift list expected 2, got {}".format(len(r["drift"])))
    # (b) 源欠如: registry 無し temp root → 観測継続
    d = tempfile.mkdtemp(prefix="agentlog-rd-")
    try:
        empty = run_routine_drift({"root": d, "days": 30})
        if empty["source_ok"] is not False:
            fails.append("missing registry should be source_ok False")
        if "観測継続" not in render_routine_drift(empty):
            fails.append("missing render should say 観測継続")
    finally:
        _rmtree(d)
    return fails


# ==========================================================================
# ディスパッチ表
# ==========================================================================
SUBCOMMANDS = {
    "tool-pattern": (run_tool_pattern, render_tool_pattern, selftest_tool_pattern),
    "retry": (run_retry, render_retry, selftest_retry),
    "cost": (run_cost, render_cost, selftest_cost),
    "denials": (run_denials, render_denials, selftest_denials),
    "subagent": (run_subagent, render_subagent, selftest_subagent),
    "context-bloat": (run_context_bloat, render_context_bloat, selftest_context_bloat),
    "session-hygiene": (run_session_hygiene, render_session_hygiene, selftest_session_hygiene),
    "unverified-claims": (run_unverified_claims, render_unverified_claims, selftest_unverified_claims),
    "rework": (run_rework, render_rework, selftest_rework),
    "mistake-tags": (run_mistake_tags, render_mistake_tags, selftest_mistake_tags),
    "routine-drift": (run_routine_drift, render_routine_drift, selftest_routine_drift),
}
# 決定論のため self-test は登録順で回す
SUB_ORDER = ["tool-pattern", "retry", "cost", "denials", "subagent", "context-bloat",
             "session-hygiene", "unverified-claims", "rework", "mistake-tags", "routine-drift"]


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------------------
# 引数パース
# --------------------------------------------------------------------------
def parse_args(argv):
    opts = {"sub": None, "json": False, "selftest": False, "log_dir": None,
            "transcript": None, "projects_dir": None, "root": None, "days": 30}
    i, positional = 0, []
    while i < len(argv):
        t = argv[i]
        if t == "--json":
            opts["json"] = True
        elif t == "--self-test":
            opts["selftest"] = True
        elif t == "--log-dir":
            i += 1; opts["log_dir"] = argv[i]
        elif t == "--transcript":
            i += 1; opts["transcript"] = argv[i]
        elif t == "--projects-dir":
            i += 1; opts["projects_dir"] = argv[i]
        elif t == "--root":
            i += 1; opts["root"] = argv[i]
        elif t == "--days":
            i += 1; opts["days"] = int(argv[i])
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
    # 既定値解決
    opts["root"] = opts["root"] or REPO_ROOT
    if opts["log_dir"] is None:
        opts["log_dir"] = os.path.join(opts["root"], ".claude", "logs")
    return opts


def run_all_selftests():
    print("agentlog self-test — 全 {} サブコマンド (hermetic fixture)".format(len(SUB_ORDER)))
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

    # 全体 self-test（サブコマンド無し + --self-test）
    if opts["selftest"] and sub is None:
        return run_all_selftests()

    if sub not in SUBCOMMANDS:
        sys.stderr.write("unknown subcommand: {}\n".format(sub))
        sys.stderr.write("available: {}\n".format(", ".join(SUB_ORDER)))
        return 2

    run_fn, render_fn, selftest_fn = SUBCOMMANDS[sub]

    # サブコマンド単体 self-test
    if opts["selftest"]:
        fails = selftest_fn()
        if fails:
            sys.stderr.write("{} self-test: FAIL\n".format(sub))
            for f in fails:
                sys.stderr.write("  - {}\n".format(f))
            return 1
        sys.stdout.write("{} self-test: PASS\n".format(sub))
        return 0

    # 通常実行（観測ツールは常に exit0・源欠如は観測継続）
    result = run_fn(opts)
    if opts["json"]:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
    else:
        sys.stdout.write(render_fn(result) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
