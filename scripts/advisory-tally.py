#!/usr/bin/env python3
"""advisory-tally.py — advisory 発火ログの hook 別 / warn 別集計（原則 P1 B3・crew #760）

`.claude/logs/advisory-fires.jsonl`（post-commit-verify.sh 等が append する
gitignore 済みローカルログ）を読み、hook 別・warn 別に発火件数を集計する。
月次 /harness-review の「advisory 昇格審査」の一次資料になる。

1 行の形式（JSON）:
  {"ts":..,"hook":..,"commit_type":..,"lines":..,"files":..,"warn":..}

重要（空≠ゼロ件）:
  ログファイルが無い / 空の場合を「advisory ゼロ件だった」と即断しない。
  ファイル未生成は「観測継続 (0 fires)」と明示し exit0 で返す（壊れではない）。

usage:
  advisory-tally.py                 # 人間可読の集計を表示
  advisory-tally.py --json          # 機械可読（JSON）で集計を表示
  advisory-tally.py --self-test     # 決定論セルフテスト（exit0=PASS / 非0=FAIL）

環境変数:
  ADVISORY_LOG_FILE   集計対象ログの差し替え（既定: <repo>/.claude/logs/advisory-fires.jsonl）
"""

import json
import os
import sys
import tempfile

import os as _os_ph
import sys as _sys_ph
_sys_ph.path.insert(0, _os_ph.path.dirname(_os_ph.path.abspath(__file__)))
import _phroot  # harness: 検査対象ルート解決

REPO_ROOT = _phroot.target_root()
DEFAULT_LOG = os.path.join(REPO_ROOT, ".claude", "logs", "advisory-fires.jsonl")


def log_path():
    return os.environ.get("ADVISORY_LOG_FILE", DEFAULT_LOG)


def tally(path):
    """ログを集計して結果 dict を返す。

    戻り値:
      {
        "log": <path>, "exists": bool,
        "total": int,            # 正常にパースできた発火行数
        "malformed": int,        # JSON パース不能だった行数（空行は除外）
        "by_hook": {hook: n},
        "by_warn": {warn: n},
      }
    exists=False（ファイル無し）でも例外を投げず total=0 で返す。
    """
    result = {
        "log": path,
        "exists": os.path.exists(path),
        "total": 0,
        "malformed": 0,
        "by_hook": {},
        "by_warn": {},
    }
    if not result["exists"]:
        return result

    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue  # 空行はスキップ（malformed に数えない）
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                result["malformed"] += 1
                continue
            if not isinstance(rec, dict):
                result["malformed"] += 1
                continue
            result["total"] += 1
            hook = rec.get("hook", "unknown") or "unknown"
            warn = rec.get("warn", "unknown") or "unknown"
            result["by_hook"][hook] = result["by_hook"].get(hook, 0) + 1
            result["by_warn"][warn] = result["by_warn"].get(warn, 0) + 1
    return result


def _sorted_desc(counts):
    # 件数降順 → 同数はキー昇順で安定化
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def render_human(r):
    lines = []
    lines.append("advisory-fire-tally — {}".format(r["log"]))
    if not r["exists"]:
        lines.append("ログ未生成・観測継続 (0 fires)。集計対象なし（欠如≠ゼロ件）。")
        return "\n".join(lines)
    lines.append("total fires: {}".format(r["total"]))
    if r["malformed"]:
        lines.append("malformed lines (skipped): {}".format(r["malformed"]))
    if r["total"] == 0:
        lines.append("ファイルは存在するが有効な発火行なし・観測継続 (0 fires)。")
        return "\n".join(lines)
    lines.append("")
    lines.append("by hook:")
    for hook, n in _sorted_desc(r["by_hook"]):
        lines.append("  {:>4}  {}".format(n, hook))
    lines.append("")
    lines.append("by warn:")
    for warn, n in _sorted_desc(r["by_warn"]):
        lines.append("  {:>4}  {}".format(n, warn))
    return "\n".join(lines)


def self_test():
    """hermetic セルフテスト。temp fixture で集計正当性と欠如耐性を assert する。"""
    failures = []

    # --- fixture: warn 2 種・計 3 行（hook も 2 種混在させる） ---
    fd, tmp = tempfile.mkstemp(suffix=".jsonl", prefix="advisory-tally-test-")
    os.close(fd)
    try:
        rows = [
            {"ts": "2026-07-04T00:00:00Z", "hook": "post-commit-verify",
             "commit_type": "feat", "lines": 900, "files": 3, "warn": "WARN-A"},
            {"ts": "2026-07-04T01:00:00Z", "hook": "post-commit-verify",
             "commit_type": "refactor", "lines": 810, "files": 2, "warn": "WARN-A"},
            {"ts": "2026-07-04T02:00:00Z", "hook": "pre-status-verify-guard",
             "commit_type": "docs", "lines": 40, "files": 1, "warn": "WARN-B"},
        ]
        with open(tmp, "w", encoding="utf-8") as fh:
            # 空行・壊れ行も混ぜて頑健性を確認（total には数えない/ malformed=1）
            fh.write("\n")
            for r in rows:
                fh.write(json.dumps(r) + "\n")
            fh.write("{ this is not valid json }\n")

        got = tally(tmp)
        if got["total"] != 3:
            failures.append("total expected 3, got {}".format(got["total"]))
        if got["malformed"] != 1:
            failures.append("malformed expected 1, got {}".format(got["malformed"]))
        if got["by_warn"] != {"WARN-A": 2, "WARN-B": 1}:
            failures.append("by_warn mismatch: {}".format(got["by_warn"]))
        if got["by_hook"] != {"post-commit-verify": 2, "pre-status-verify-guard": 1}:
            failures.append("by_hook mismatch: {}".format(got["by_hook"]))
        if not got["exists"]:
            failures.append("exists expected True for present fixture")

        # 降順ソートの先頭が最多 warn/hook であること
        top_warn = _sorted_desc(got["by_warn"])[0][0]
        if top_warn != "WARN-A":
            failures.append("top warn expected WARN-A, got {}".format(top_warn))
    finally:
        os.remove(tmp)

    # --- 欠如パス: 例外を投げず exists=False / total=0 で返る ---
    missing = os.path.join(tempfile.gettempdir(), "advisory-tally-nonexistent-xyz.jsonl")
    if os.path.exists(missing):
        os.remove(missing)
    try:
        got_missing = tally(missing)
    except Exception as exc:  # noqa: BLE001
        failures.append("missing path raised: {!r}".format(exc))
        got_missing = None
    if got_missing is not None:
        if got_missing["exists"] is not False:
            failures.append("missing exists expected False")
        if got_missing["total"] != 0:
            failures.append("missing total expected 0, got {}".format(got_missing["total"]))
        if "観測継続" not in render_human(got_missing):
            failures.append("missing render should state 観測継続")

    if failures:
        sys.stderr.write("advisory-tally self-test: FAIL\n")
        for f in failures:
            sys.stderr.write("  - {}\n".format(f))
        return 1
    sys.stdout.write("advisory-tally self-test: PASS (3-line fixture + missing-path)\n")
    return 0


def main(argv):
    args = argv[1:]
    if "--self-test" in args:
        return self_test()

    r = tally(log_path())
    if "--json" in args:
        sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(render_human(r) + "\n")
    # 集計は常に成功扱い（欠如は壊れではない・fail-open 観測ツール）
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
