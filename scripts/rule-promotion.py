#!/usr/bin/env python3
# =============================================================
# rule-promotion.py — advisory→blocking(H3) 昇格候補の抽出
#
# advisory フック(fail-open, exit0)が同種の warn を繰り返し発火する状況は、
# その逸脱が「たまたま」でなく「習慣」であることの決定論的証拠になる。
# 本スクリプトは advisory-fires.jsonl を warn 別に集計し、再発回数が
# --recur(既定 2)以上の warn を H3(BLOCK フック)への昇格候補として列挙する。
# harness-engineering KPI「advisory→blocking 昇格数 0/7→増」の観測手段。
#
# 入力: .claude/logs/advisory-fires.jsonl（ADVISORY_LOG_FILE で差替可）
#        各行 = {"ts","hook","commit_type","lines","files","warn"} の JSON
#        （post-commit-verify.sh 等 advisory フックが追記する形式）
# 出力: 昇格候補 = 再発回数 >= --recur の warn 一覧（--json でも可）
#
# 設計:
#   - ログ欠如/空 → 「候補 0・観測継続」を exit0 で明示（観測不足は失敗でない）
#   - JSON 破損行はスキップ（fail-open）。warn 空の行は集計対象外
#   - 依存は Python3 標準ライブラリのみ
# =============================================================
import argparse
import json
import os
import sys
import tempfile
from collections import Counter

import os as _os_ph
import sys as _sys_ph
_sys_ph.path.insert(0, _os_ph.path.dirname(_os_ph.path.abspath(__file__)))
import _phroot  # harness: 検査対象ルート解決

DEFAULT_LOG = os.path.join(
    _phroot.target_root(),
    ".claude", "logs", "advisory-fires.jsonl",
)


def log_path():
    """ADVISORY_LOG_FILE で差替可能な advisory ログのパス。"""
    return os.environ.get("ADVISORY_LOG_FILE", DEFAULT_LOG)


def load_fires(path):
    """advisory-fires.jsonl を読み、warn を持つレコードのリストを返す。

    破損行はスキップする(fail-open)。ファイル欠如は空リスト。
    """
    fires = []
    if not path or not os.path.exists(path):
        return fires
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(rec, dict):
                continue
            warn = rec.get("warn")
            if isinstance(warn, str) and warn.strip():
                fires.append(rec)
    return fires


def tally(fires):
    """warn 別の再発回数 Counter。"""
    return Counter(rec["warn"] for rec in fires)


def extract_candidates(counter, recur):
    """再発回数 >= recur の (warn, count) を回数降順→warn 昇順で返す。"""
    cands = [(w, c) for w, c in counter.items() if c >= recur]
    cands.sort(key=lambda wc: (-wc[1], wc[0]))
    return cands


def report(path, recur, as_json):
    fires = load_fires(path)
    counter = tally(fires)
    candidates = extract_candidates(counter, recur)

    if as_json:
        print(json.dumps({
            "log": path,
            "log_present": bool(path) and os.path.exists(path),
            "recur_threshold": recur,
            "total_fires": len(fires),
            "distinct_warns": len(counter),
            "candidates": [{"warn": w, "recur": c} for w, c in candidates],
        }, ensure_ascii=False))
        return 0

    if not (path and os.path.exists(path)):
        print(f"[rule-promotion] ログ未生成 ({path}) — 候補 0・観測継続")
        return 0
    if not fires:
        print(f"[rule-promotion] 発火 0 件 ({path}) — 候補 0・観測継続")
        return 0
    if not candidates:
        print(f"[rule-promotion] 発火 {len(fires)} 件 / distinct {len(counter)} — "
              f"再発 >= {recur} の昇格候補なし・観測継続")
        return 0

    print(f"[rule-promotion] 昇格候補 (再発 >= {recur}) — "
          f"H3(BLOCK) フック化 or common-mistakes.md 昇格を検討:")
    for w, c in candidates:
        print(f"  - ({c}回) {w}")
    return 0


def self_test():
    """hermetic self-test: 同一 warn 2 回=候補 / 別 warn 1 回=非候補 を検証。

    temp fixture を作り、抽出結果が期待通りかを assert する。exit0=pass。
    """
    failures = []
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
    try:
        # warn "AAA" を 2 回, "BBB" を 1 回。加えて破損行・warn 空行も混ぜる。
        rows = [
            {"ts": "t1", "hook": "post-commit-verify", "warn": "AAA fat commit"},
            {"ts": "t2", "hook": "post-commit-verify", "warn": "BBB mixed type"},
            {"ts": "t3", "hook": "post-commit-verify", "warn": "AAA fat commit"},
            {"ts": "t4", "hook": "post-commit-verify", "warn": ""},   # 空 → 無視
        ]
        for r in rows:
            tmp.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp.write("{ this is broken json \n")  # 破損 → skip
        tmp.close()

        fires = load_fires(tmp.name)
        counter = tally(fires)

        # 空 warn と破損行が除外され、有効 3 件のはず
        if len(fires) != 3:
            failures.append(f"expected 3 valid fires, got {len(fires)}")

        # recur=2: AAA のみ候補
        cands2 = extract_candidates(counter, 2)
        got2 = [w for w, _ in cands2]
        if got2 != ["AAA fat commit"]:
            failures.append(f"recur=2 expected ['AAA fat commit'], got {got2}")
        if dict(cands2).get("AAA fat commit") != 2:
            failures.append("AAA recur count expected 2")

        # BBB(1回) は候補に含まれてはならない
        if "BBB mixed type" in got2:
            failures.append("BBB(1回) must NOT be a candidate at recur=2")

        # recur=3: 候補なし(AAA も 2 回で閾値未満)
        cands3 = extract_candidates(counter, 3)
        if cands3:
            failures.append(f"recur=3 expected no candidates, got {cands3}")

        # 欠如ログ: 候補 0 で exit0(=report が 0 を返す)
        missing = os.path.join(os.path.dirname(tmp.name), "no-such-advisory.jsonl")
        rc = report(missing, 2, as_json=True)
        if rc != 0:
            failures.append(f"missing-log report expected exit0, got {rc}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    if failures:
        for f in failures:
            sys.stderr.write(f"[self-test FAIL] {f}\n")
        sys.stderr.write(f"self-test: {len(failures)} assertion(s) failed\n")
        return 1
    print("[rule-promotion] self-test PASS "
          "(同一warn2回=候補 / 別warn1回=非候補 / 欠如=候補0 exit0)")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description="advisory-fires.jsonl から blocking(H3) 昇格候補を抽出")
    p.add_argument("--recur", type=int, default=2,
                   help="昇格候補とみなす最小再発回数(既定 2)")
    p.add_argument("--json", action="store_true", help="JSON で出力")
    p.add_argument("--self-test", action="store_true",
                   help="hermetic self-test を実行(exit0=pass)")
    args = p.parse_args(argv)

    if args.self_test:
        return self_test()
    return report(log_path(), args.recur, args.json)


if __name__ == "__main__":
    sys.exit(main())
