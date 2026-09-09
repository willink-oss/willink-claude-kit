#!/usr/bin/env python3
# =============================================================
# secret-scan-audit.py — D02 secret-scan-hardening
#
# 目的: 与えた secret 正規表現セット（＝ policy）の「既知 API キー形式」に対する
#       検出網羅率を **監査（read）** し、未カバー形式と追加パターン案を **印字（提案）** する。
#
# 非目標（安全制約・self-lockout/L3 防止）:
#   - 既存の security フック（.claude/hooks/pre-commit-quality.sh 等）を **一切改変しない**。
#     本スクリプトは corpus と policy を突き合わせて監査するだけで、フックへの書込みをしない。
#   - パターンの実際の適用（フック編集）は **人手**。security フック（fail-closed）は harness gate の
#     一部なので、追加は 責任者 レビュー後に手動で行う（自己適用禁止・原則 P3）。
#
# 使い方:
#   python3 scripts/secret-scan-audit.py                 # 内蔵 DEFAULT policy を監査
#   python3 scripts/secret-scan-audit.py --patterns-file <f>  # 1 行 1 正規表現の policy を監査
#   python3 scripts/secret-scan-audit.py --self-test     # 決定論 --check（hermetic）
#
# exit code（監査モード）:
#   0 = 全形式カバー（gap なし）
#   1 = 未カバー形式あり（提案を印字）
#   2 = usage / read エラー
# exit code（--self-test）:
#   0 = pass / 1 = fail
#
# DEFAULT_POLICY は .claude/hooks/pre-commit-quality.sh の SECRET_PATTERNS を
# Python 正規表現へ写像した **読み取り専用ミラー**（POSIX [[:space:]] → \s 等を変換）。
# フック本体は改変しない。両者が乖離したら本ミラーを更新する（フックが正）。
# =============================================================

import argparse
import re
import sys


# --- 既知 API キー形式の corpus -------------------------------------------------
# 各要素: id / desc / sample（形式的に妥当なダミー・実キーではない） / suggest（追加提案用の正規表現）
# sample は「良い検出パターンなら必ずマッチすべき代表文字列」。網羅率はこの sample を用いて算出する。
KNOWN_FORMATS = [
    # --- 現行フックが既にカバーしている想定の形式 ---
    {"id": "aws_access_key_id", "desc": "AWS Access Key ID",
     "sample": "AKIA" + "IOSFODNN7EXAMPLE",  # pragma: allowlist secret
     "suggest": r"AKIA[0-9A-Z]{16}"},  # pragma: allowlist secret
    {"id": "openai_legacy", "desc": "OpenAI API key (legacy sk-)",
     "sample": "sk-" + "abc123DEF456ghi789JKL012mno345pqr678",  # pragma: allowlist secret
     "suggest": r"sk-[A-Za-z0-9]{20,}"},  # pragma: allowlist secret
    {"id": "openai_project", "desc": "OpenAI project key (sk-proj-)",  # pragma: allowlist secret
     "sample": "sk-proj-" + "abc123DEF456ghi789JKL012mno345pqrST",  # pragma: allowlist secret
     "suggest": r"sk-proj-[A-Za-z0-9_-]{20,}"},  # pragma: allowlist secret
    {"id": "anthropic_api", "desc": "Anthropic API key (sk-ant-)",  # pragma: allowlist secret
     "sample": "sk-ant-" + "api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",  # pragma: allowlist secret
     "suggest": r"sk-ant-[A-Za-z0-9_-]{20,}"},  # pragma: allowlist secret
    {"id": "github_pat_classic", "desc": "GitHub PAT (classic, ghp_)",  # pragma: allowlist secret
     "sample": "ghp_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8",  # 36 chars tail  # pragma: allowlist secret
     "suggest": r"ghp_[A-Za-z0-9]{36}"},  # pragma: allowlist secret
    {"id": "github_oauth", "desc": "GitHub OAuth token (gho_)",  # pragma: allowlist secret
     "sample": "gho_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8",  # pragma: allowlist secret
     "suggest": r"gho_[A-Za-z0-9]{36}"},  # pragma: allowlist secret
    {"id": "github_app_server", "desc": "GitHub App server token (ghs_)",
     "sample": "ghs_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8",  # pragma: allowlist secret
     "suggest": r"ghs_[A-Za-z0-9]{36}"},  # pragma: allowlist secret
    {"id": "github_pat_fine", "desc": "GitHub fine-grained PAT (github_pat_)",
     "sample": "github_pat_" + "1A" + "2b3c4d5e6f7g8h9i0j" + "1k2l3m4n5o6p7q8r9s0t" + "1u2v3w4x5y6z7A8B9C0D",  # pragma: allowlist secret
     "suggest": r"github_pat_[A-Za-z0-9_]{50,}"},  # pragma: allowlist secret
    {"id": "google_api_key", "desc": "Google API key (AIza)",  # pragma: allowlist secret
     "sample": "AIza" + "SyD-abc123DEF456ghi789JKL012mno34_x",  # 35 tail  # pragma: allowlist secret
     "suggest": r"AIza[0-9A-Za-z_-]{35}"},  # pragma: allowlist secret
    {"id": "slack_bot", "desc": "Slack bot token (xoxb-)",  # pragma: allowlist secret
     "sample": "xoxb-" + "1234567890-0987654321-AbCdEfGhIjKlMnOpQrStUvWx",  # pragma: allowlist secret
     "suggest": r"xox[baprs]-[0-9a-zA-Z-]{10,}"},  # pragma: allowlist secret
    {"id": "slack_user", "desc": "Slack user token (xoxp-)",
     "sample": "xoxp-" + "1234567890-0987654321-AbCdEfGhIjKlMnOpQrStUvWx",  # pragma: allowlist secret
     "suggest": r"xox[baprs]-[0-9a-zA-Z-]{10,}"},  # pragma: allowlist secret
    {"id": "stripe_live_secret", "desc": "Stripe live secret (sk_live_)",
     "sample": "sk_live_" + "51AbCdEfGhIjKlMnOpQrStUvWx",  # pragma: allowlist secret
     "suggest": r"sk_live_[a-zA-Z0-9]{20,}"},  # pragma: allowlist secret
    {"id": "stripe_test_secret", "desc": "Stripe test secret (sk_test_)",
     "sample": "sk_test_" + "51AbCdEfGhIjKlMnOpQrStUvWx",  # pragma: allowlist secret
     "suggest": r"sk_test_[a-zA-Z0-9]{20,}"},  # pragma: allowlist secret
    {"id": "stripe_restricted", "desc": "Stripe restricted key (rk_live_)",
     "sample": "rk_live_" + "51AbCdEfGhIjKlMnOpQrStUvWx",  # pragma: allowlist secret
     "suggest": r"rk_live_[a-zA-Z0-9]{20,}"},  # pragma: allowlist secret
    {"id": "stripe_webhook", "desc": "Stripe webhook signing secret (whsec_)",
     "sample": "whsec_" + "AbCdEfGhIjKlMnOpQrStUvWx",  # pragma: allowlist secret
     "suggest": r"whsec_[a-zA-Z0-9]{20,}"},  # pragma: allowlist secret
    {"id": "supabase_service_role", "desc": "Supabase service role (sb_secret_)",
     "sample": "sb_secret_" + "AbCdEfGhIjKlMnOpQrStUvWx",  # pragma: allowlist secret
     "suggest": r"sb_secret_[a-zA-Z0-9_-]{20,}"},  # pragma: allowlist secret
    {"id": "pem_private_key", "desc": "PEM private key block",
     "sample": "-----BEGIN " + "RSA PRIVATE KEY-----",  # pragma: allowlist secret
     "suggest": r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"},  # pragma: allowlist secret

    # --- 現行フックに未カバーの可能性が高い形式（監査で gap として検出・提案されるべきもの） ---
    {"id": "slack_app_level", "desc": "Slack app-level token (xapp-)",
     "sample": "xapp-" + "1-A012BCDEF34-1234567890123-abcdef0123456789abcdef0123456789",  # pragma: allowlist secret
     "suggest": r"xapp-[0-9]-[A-Z0-9]+-[0-9]+-[a-f0-9]+"},  # pragma: allowlist secret
    {"id": "github_refresh", "desc": "GitHub refresh token (ghr_)",
     "sample": "ghr_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8",  # pragma: allowlist secret
     "suggest": r"ghr_[A-Za-z0-9]{36,}"},  # pragma: allowlist secret
    {"id": "gitlab_pat", "desc": "GitLab personal access token (glpat-)",  # pragma: allowlist secret
     "sample": "glpat-" + "AbCdEf1234GhIjKl5678",  # pragma: allowlist secret
     "suggest": r"glpat-[A-Za-z0-9_-]{20}"},  # pragma: allowlist secret
    {"id": "npm_token", "desc": "npm access token (npm_)",
     "sample": "npm_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8",  # pragma: allowlist secret
     "suggest": r"npm_[A-Za-z0-9]{36}"},  # pragma: allowlist secret
    {"id": "sendgrid_key", "desc": "SendGrid API key (SG.)",
     "sample": "SG." + "AbCdEfGhIjKlMnOpQrStUv.AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfg",  # pragma: allowlist secret
     "suggest": r"SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}"},  # pragma: allowlist secret
    {"id": "twilio_api_key", "desc": "Twilio API key SID (SK + 32 hex)",
     "sample": "SK" + "0123456789abcdef0123456789abcdef",  # pragma: allowlist secret
     "suggest": r"SK[0-9a-fA-F]{32}"},  # pragma: allowlist secret
    {"id": "huggingface_token", "desc": "Hugging Face token (hf_)",
     "sample": "hf_" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",  # pragma: allowlist secret
     "suggest": r"hf_[A-Za-z0-9]{34,}"},  # pragma: allowlist secret
    {"id": "google_oauth_client_secret", "desc": "Google OAuth client secret (GOCSPX-)",
     "sample": "GOCSPX-" + "AbCdEfGhIjKlMnOpQrStUvWx1234",  # pragma: allowlist secret
     "suggest": r"GOCSPX-[A-Za-z0-9_-]{28}"},  # pragma: allowlist secret
]


# --- DEFAULT policy: pre-commit-quality.sh の SECRET_PATTERNS の Python ミラー ---
# フック本体は改変しない。POSIX [[:space:]] → \s、シェルクォート → Python 文字列へ写像済み。
# 各要素は独立した正規表現（フックは 1 本の巨大 alternation だが、監査上は等価）。
DEFAULT_POLICY = [
    r"AKIA[0-9A-Z]{16}",  # pragma: allowlist secret
    r"(^|[^A-Za-z0-9_-])sk-[A-Za-z0-9_-]*[A-Za-z0-9_]{20,}",
    r"sk_live_[a-zA-Z0-9]{20,}",  # pragma: allowlist secret
    r"sk_test_[a-zA-Z0-9]{20,}",  # pragma: allowlist secret
    r"rk_live_[a-zA-Z0-9]{20,}",  # pragma: allowlist secret
    r"whsec_[a-zA-Z0-9]{20,}",  # pragma: allowlist secret
    r"sb_secret_[a-zA-Z0-9_-]{20,}",  # pragma: allowlist secret
    r"ghp_[a-zA-Z0-9]{36}",  # pragma: allowlist secret
    r"gho_[a-zA-Z0-9]{36}",  # pragma: allowlist secret
    r"ghs_[a-zA-Z0-9]{36}",  # pragma: allowlist secret
    r"github_pat_[a-zA-Z0-9_]{50,}",  # pragma: allowlist secret
    r"AIza[0-9A-Za-z_-]{35}",  # pragma: allowlist secret
    r"xox[baprs]-[0-9a-zA-Z-]{10,}",  # pragma: allowlist secret
    r"aws[_-]?(secret|access)[_A-Za-z]*\s*[:=]\s*[\"']?[A-Za-z0-9/+]{40}",  # pragma: allowlist secret
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----",
    r"(password|passwd|api[_-]?key|secret|token)\s*[:=]\s*[\"'][^\"']{8,}",
]


def audit(corpus, patterns):
    """corpus の各形式 sample が patterns のいずれかにマッチするか監査する。

    戻り値: (covered, uncovered, invalid)
      covered   = [(fmt, matched_pattern_src), ...]
      uncovered = [fmt, ...]
      invalid   = [(pattern_src, error), ...]  # コンパイル不能な policy 行（graceful skip）
    """
    compiled = []
    invalid = []
    for src in patterns:
        try:
            compiled.append((src, re.compile(src, re.IGNORECASE)))
        except re.error as exc:
            invalid.append((src, str(exc)))

    covered = []
    uncovered = []
    for fmt in corpus:
        hit = None
        for src, rx in compiled:
            if rx.search(fmt["sample"]):
                hit = src
                break
        if hit is not None:
            covered.append((fmt, hit))
        else:
            uncovered.append(fmt)
    return covered, uncovered, invalid


def literal_findings(text, corpus):
    """`text` の中に、corpus の形式そのものに見える**連続した文字列**が無いか見る。純関数。

    なぜ要るか: 我々の `# pragma: allowlist secret` は自前の秘匿ゲートにしか効かない。
    公開先（GitHub push protection 等）は pragma を尊重せず、literal の見た目だけで弾く。
    つまり pragma は「手元は緑・公開先で赤」を作る。**pragma を無視して**見るのがこの関数。

    ダミー鍵を corpus に置く回避策は「固定 prefix の直後で連結を切る」こと
    （"SG." + "AbCd..." のように書く。実行時の値は変わらない）。

    返り値: [(fmt_id, line_no, matched_text), ...]
    """
    findings = []
    lines = text.split("\n")
    for fmt in corpus:
        try:
            rx = re.compile(fmt["suggest"])
        except re.error:
            continue          # 壊れた提案は audit() 側が invalid として数える
        for i, line in enumerate(lines, 1):
            m = rx.search(line)
            if m:
                findings.append((fmt["id"], i, m.group(0)))
    return findings


def format_report(corpus, covered, uncovered, invalid):
    total = len(corpus)
    ncov = len(covered)
    pct = (100.0 * ncov / total) if total else 100.0
    lines = []
    lines.append("=== secret-scan-audit ===")
    lines.append("既知形式: %d / カバー: %d / 未カバー: %d / 網羅率: %.1f%%"
                 % (total, ncov, len(uncovered), pct))
    if invalid:
        lines.append("")
        lines.append("[warn] コンパイル不能な policy パターン %d 件（監査から除外）:" % len(invalid))
        for src, err in invalid:
            lines.append("  - %s  (%s)" % (src, err))
    if uncovered:
        lines.append("")
        lines.append("[GAP] 未カバー形式（フック追加を提案・適用は人手/責任者 レビュー）:")
        for fmt in uncovered:
            lines.append("  - %-28s %s" % (fmt["id"], fmt["desc"]))
            lines.append("      提案パターン: %s" % fmt["suggest"])
    else:
        lines.append("")
        lines.append("[OK] 未カバー形式なし（全形式カバー）")
    return "\n".join(lines)


def load_patterns_file(path):
    patterns = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(line)
    return patterns


# --- self-test（hermetic・内蔵 fixture で網羅率算出ロジックを検証・原則 P1 ハードコード成功禁止） ---
def run_self_test():
    # fixture corpus: 4 形式。DEFAULT_POLICY とは独立（audit 関数の計算そのものを検証する）。
    fixture_corpus = [
        {"id": "cov_alpha", "desc": "alpha", "sample": "TOKEN-AAAA", "suggest": r"TOKEN-[A-Z]+"},  # pragma: allowlist secret
        {"id": "cov_beta",  "desc": "beta",  "sample": "KEY_1234",   "suggest": r"KEY_[0-9]+"},  # pragma: allowlist secret
        {"id": "gap_gamma", "desc": "gamma", "sample": "ZZZ-9999",   "suggest": r"ZZZ-[0-9]+"},  # pragma: allowlist secret
        {"id": "gap_delta", "desc": "delta", "sample": "QQ_beef",    "suggest": r"QQ_[a-f]+"},  # pragma: allowlist secret
    ]

    partial = [r"TOKEN-[A-Z]+", r"KEY_[0-9]+"]       # 2/4 をカバー
    full = partial + [r"ZZZ-[0-9]+", r"QQ_[a-f]+"]   # 4/4 をカバー
    empty = []                                        # 0/4
    with_bad = partial + [r"[invalid("]              # 不正正規表現混入でも 2/4 を維持

    checks = []

    # case 1: 部分カバー → covered=2, uncovered={gap_gamma,gap_delta}
    cov, unc, inv = audit(fixture_corpus, partial)
    ok1 = (len(cov) == 2
           and set(f["id"] for f in unc) == {"gap_gamma", "gap_delta"}
           and inv == [])
    checks.append(("partial 2/4", ok1))

    # case 2: 完全カバー → covered=4, uncovered={}
    cov, unc, inv = audit(fixture_corpus, full)
    ok2 = (len(cov) == 4 and unc == [] and inv == [])
    checks.append(("full 4/4", ok2))

    # case 3: 空 policy → covered=0, uncovered=全件
    cov, unc, inv = audit(fixture_corpus, empty)
    ok3 = (len(cov) == 0 and len(unc) == 4)
    checks.append(("empty 0/4", ok3))

    # case 4: 不正パターンを graceful skip し、有効分で 2/4 を維持
    cov, unc, inv = audit(fixture_corpus, with_bad)
    ok4 = (len(cov) == 2 and len(inv) == 1
           and set(f["id"] for f in unc) == {"gap_gamma", "gap_delta"})
    checks.append(("invalid-pattern graceful", ok4))

    # case 5: 実 corpus の健全性 — 各形式の suggest 提案は自身の sample にマッチすべき
    #（提案パターンが自形式を検出できないと監査の提案が無意味になる回帰を防ぐ）
    ok5 = True
    for fmt in KNOWN_FORMATS:
        try:
            if not re.search(fmt["suggest"], fmt["sample"], re.IGNORECASE):
                ok5 = False
                sys.stderr.write("[self-test] suggest miss: %s\n" % fmt["id"])
        except re.error as exc:
            ok5 = False
            sys.stderr.write("[self-test] suggest bad regex %s: %s\n" % (fmt["id"], exc))
    checks.append(("suggest self-match", ok5))

    # case 6-8: ダミー鍵が source 上で連続literal になっていないか
    #（公開先の push protection は我々の pragma を尊重しない。ここで手元に落とす）
    sg = [f for f in KNOWN_FORMATS if f["id"] == "sendgrid_key"]
    # block 側: 連続literal は検出される
    bad = '     "sample": "SG.' + 'A' * 22 + '.' + 'B' * 43 + '",  # pragma: allowlist secret'
    ok6 = len(literal_findings(bad, sg)) == 1
    checks.append(("literal block", ok6))
    # pass 側: 無関係な行は検出しない
    ok7 = literal_findings('     "desc": "SendGrid API key (SG.)",', sg) == []
    checks.append(("literal pass", ok7))
    # 代替手順側: このゲートが案内する書き方（prefix 直後で切る）が実際に通ること
    good = '     "sample": "SG." + "' + 'A' * 22 + '.' + 'B' * 43 + '",'
    ok8 = literal_findings(good, sg) == []
    checks.append(("literal split-ok", ok8))

    # case 9: 自分自身の source が上の不変条件を満たしている（実測・分母は corpus 全件）
    ok9 = True
    try:
        with open(__file__, encoding="utf-8") as fh:
            own = fh.read()
    except OSError as exc:
        ok9 = False           # 読めない＝不明。空を 0 件と書かない
        sys.stderr.write("[self-test] own source unreadable: %s\n" % exc)
    else:
        for fid, ln, hit in literal_findings(own, KNOWN_FORMATS):
            ok9 = False
            sys.stderr.write("[self-test] contiguous literal %s:%d (%s)\n"
                             % (fid, ln, hit[:12] + "..."))
    checks.append(("own source %d formats" % len(KNOWN_FORMATS), ok9))

    all_ok = True
    for name, ok in checks:
        print("[self-test] %-26s -> %s" % (name, "OK" if ok else "FAIL"))
        all_ok = all_ok and ok

    if all_ok:
        print("[self-test] RESULT: PASS")
        return 0
    print("[self-test] RESULT: FAIL")
    return 1


def main():
    ap = argparse.ArgumentParser(
        description="secret 正規表現セットの既知 API キー形式に対する検出網羅率を監査し、"
                    "未カバー形式と追加パターン案を提案する（監査 + 提案のみ・適用は人手）"
    )
    ap.add_argument("--patterns-file",
                    help="監査対象 policy（1 行 1 正規表現・# はコメント）。未指定なら内蔵 DEFAULT policy")
    ap.add_argument("--self-test", action="store_true",
                    help="内蔵 fixture で網羅率算出ロジックを検証（hermetic・決定論 --check）")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(run_self_test())

    if args.patterns_file:
        try:
            patterns = load_patterns_file(args.patterns_file)
        except OSError as exc:
            sys.stderr.write("read error: %s\n" % exc)
            sys.exit(2)
        source = args.patterns_file
    else:
        patterns = DEFAULT_POLICY
        source = "DEFAULT_POLICY (pre-commit-quality.sh mirror)"

    covered, uncovered, invalid = audit(KNOWN_FORMATS, patterns)
    print("policy source: %s (%d patterns)" % (source, len(patterns)))
    print(format_report(KNOWN_FORMATS, covered, uncovered, invalid))

    # 未カバーあり = 監査で gap 検出（提案を印字）。フック適用は人手・責任者 レビュー後。
    sys.exit(1 if uncovered else 0)


if __name__ == "__main__":
    main()
