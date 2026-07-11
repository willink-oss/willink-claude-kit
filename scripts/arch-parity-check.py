#!/usr/bin/env python3
# =============================================================
# arch-parity-check.py — architecture-parity-gate
#
# Goal (generic architecture gate): given a config (JSON) that declares the
# "architectural intent" =
#   (1) allowed dependency directions between layers (allowed_dependencies, a whitelist)
#   (2) per-layer file naming rules (naming)
#   detect VIOLATIONS in a code tree (--root). Any violation => exit 1.
#
#   "parity" = mechanically compare the declared design (config) against the
#   implementation (tree). Language-agnostic and config-driven (import detection
#   is delegated to the config's import_markers regexes).
#
# Non-goals (safety constraints):
#   - DETECTION ONLY. Never rewrites code, never applies a fix, never deletes.
#     Fixing a violation is a human's job (done in a PR). The gate does not repair.
#   - Wiring this gate as a CI required status check / branch protection is a
#     deliberate, higher-risk change — do it by hand, on purpose, not from this
#     script (the gate must never self-install into CI).
#   - No external API dependency (no gh / aws). Local file reads only.
#
# Usage:
#   python3 scripts/arch-parity-check.py --config <config.json> --root <dir>
#   python3 scripts/arch-parity-check.py --self-test        # deterministic --check (hermetic)
#
# Exit codes (audit mode):
#   0 = no violations (config intent matches the tree = parity OK)
#   1 = violations found (dependency and/or naming). Details are printed.
#   2 = usage / config read / parse error
# Exit codes (--self-test):
#   0 = pass (built-in fixtures: compliant tree -> 0 violations / violating tree -> as expected)
#   1 = fail (logic is broken)
#
# Config schema (every field is optional — a minimal config still runs):
#   {
#     "layers": {
#       "<layer>": {
#         "roots": ["src/domain", ...],            # path prefixes of files in this layer
#         "import_markers": ["from +domain", ...]   # regexes that mean "imports this layer"
#       }, ...
#     },
#     "allowed_dependencies": {
#       "<layer>": ["<layer>", ...]     # who this layer may depend on (whitelist; same layer always allowed)
#     },
#     "naming": [
#       {
#         "applies_to": "<layer>|*",    # target layer (default "*" = all layers)
#         "path_regex": "...",          # narrow targets further by relpath (optional)
#         "must_match": "...",          # violation if basename does NOT match this regex (optional)
#         "must_not_match": "...",      # violation if basename matches this regex (optional)
#         "message": "..."              # message shown on violation (optional)
#       }, ...
#     ],
#     "exclude": ["node_modules", ".git", "test"]   # skip files whose relpath contains any substring (optional)
#   }
# =============================================================

import argparse
import json
import os
import re
import sys


# ---------------------------------------------------------------------------
# Walk helpers
# ---------------------------------------------------------------------------

_DEFAULT_EXCLUDE = [".git/"]


def _iter_files(root, excludes):
    """List every file under root as a relpath (posix). Skip any whose relpath
    contains an exclude substring."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune .git and similar huge / irrelevant dirs (faster + deterministic).
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            rel_posix = rel.replace(os.sep, "/")
            if any(ex and ex in rel_posix for ex in excludes):
                continue
            out.append((rel_posix, full))
    out.sort()  # deterministic order
    return out


def _classify(rel_posix, layers):
    """Which layer does relpath belong to? Decide by the longest-matching root.
    Returns None if it belongs to no layer."""
    best_layer = None
    best_len = -1
    for layer, spec in layers.items():
        for r in spec.get("roots", []):
            r_norm = r.rstrip("/")
            if rel_posix == r_norm or rel_posix.startswith(r_norm + "/"):
                if len(r_norm) > best_len:
                    best_len = len(r_norm)
                    best_layer = layer
    return best_layer


def _read_text(full):
    """Read as text (undecodable files are treated as skippable -> empty string)."""
    try:
        with open(full, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Core comparison logic (same functions used by fixtures and by real runs)
# ---------------------------------------------------------------------------

def compile_markers(layers):
    """Compile each layer's import_markers into (layer, compiled_regex) pairs.
    Bad regexes are set aside as invalid."""
    compiled = []
    invalid = []
    for layer, spec in layers.items():
        for pat in spec.get("import_markers", []):
            try:
                compiled.append((layer, re.compile(pat)))
            except re.error as exc:
                invalid.append(("import_marker", layer, pat, str(exc)))
    return compiled, invalid


def check_dependencies(files, layers, allowed, markers):
    """Detect dependency-direction violations. If a file in src layer matches the
    import_markers of a target layer it is not allowed to depend on, that's a
    violation (same layer is always allowed)."""
    violations = []
    for rel, full in files:
        src_layer = _classify(rel, layers)
        if src_layer is None:
            continue
        allow = set(allowed.get(src_layer, []))
        allow.add(src_layer)  # intra-layer dependency is always allowed
        text = _read_text(full)
        if not text:
            continue
        seen = set()  # collapse duplicate violations to one per (target_layer)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for target_layer, rx in markers:
                if target_layer == src_layer:
                    continue
                if target_layer in allow:
                    continue
                if target_layer in seen:
                    continue
                if rx.search(line):
                    seen.add(target_layer)
                    violations.append({
                        "type": "dependency",
                        "file": rel,
                        "line": lineno,
                        "detail": "%s -> %s is not an allowed dependency direction (allowed: %s)"
                                  % (src_layer, target_layer,
                                     ", ".join(sorted(allow)) or "(none)"),
                    })
    return violations


def compile_naming(naming):
    """Pre-compile the regexes in the naming rules. Bad regexes are set aside as invalid."""
    compiled = []
    invalid = []
    for i, rule in enumerate(naming):
        entry = {
            "applies_to": rule.get("applies_to", "*"),
            "message": rule.get("message", ""),
            "raw": rule,
        }
        ok = True
        for key in ("path_regex", "must_match", "must_not_match"):
            if key in rule and rule[key] is not None:
                try:
                    entry[key] = re.compile(rule[key])
                except re.error as exc:
                    invalid.append(("naming[%d].%s" % (i, key), rule[key], str(exc)))
                    ok = False
        if ok:
            compiled.append(entry)
    return compiled, invalid


def check_naming(files, layers, naming_rules):
    """Detect naming violations. A basename that fails must_match / matches
    must_not_match is a violation."""
    violations = []
    for rel, full in files:
        layer = _classify(rel, layers)
        base = rel.rsplit("/", 1)[-1]
        for rule in naming_rules:
            applies = rule["applies_to"]
            if applies != "*" and applies != layer:
                continue
            if "path_regex" in rule and not rule["path_regex"].search(rel):
                continue
            if "must_match" in rule and not rule["must_match"].search(base):
                violations.append({
                    "type": "naming",
                    "file": rel,
                    "line": 0,
                    "detail": rule["message"] or ("naming rule mismatch: does not match must_match"),
                })
            if "must_not_match" in rule and rule["must_not_match"].search(base):
                violations.append({
                    "type": "naming",
                    "file": rel,
                    "line": 0,
                    "detail": rule["message"] or ("naming rule violation: matches must_not_match"),
                })
    return violations


def check_tree(root, config):
    """Compare the whole tree; return (violations, invalid_patterns)."""
    layers = config.get("layers", {})
    allowed = config.get("allowed_dependencies", {})
    naming = config.get("naming", [])
    excludes = list(_DEFAULT_EXCLUDE) + list(config.get("exclude", []))

    files = _iter_files(root, excludes)

    markers, inv1 = compile_markers(layers)
    naming_rules, inv2 = compile_naming(naming)

    violations = []
    violations += check_dependencies(files, layers, allowed, markers)
    violations += check_naming(files, layers, naming_rules)

    # Deterministic ordering (type, file, line).
    violations.sort(key=lambda v: (v["type"], v["file"], v["line"]))
    return violations, (inv1 + inv2)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def format_report(root, config, violations, invalid):
    lines = []
    layers = config.get("layers", {})
    lines.append("root: %s" % root)
    lines.append("layers: %s" % (", ".join(sorted(layers.keys())) or "(none)"))
    if invalid:
        lines.append("")
        lines.append("WARNING: invalid regex patterns (skipped — fix the config):")
        for item in invalid:
            lines.append("  - %s" % " / ".join(str(x) for x in item))
    lines.append("")
    if not violations:
        lines.append("OK: no violations (config intent matches the tree = parity OK)")
        return "\n".join(lines)
    dep = [v for v in violations if v["type"] == "dependency"]
    nam = [v for v in violations if v["type"] == "naming"]
    lines.append("FAIL: %d violation(s) (dependency %d / naming %d):" % (len(violations), len(dep), len(nam)))
    for v in violations:
        loc = "%s:%d" % (v["file"], v["line"]) if v["line"] else v["file"]
        lines.append("  [%s] %s — %s" % (v["type"], loc, v["detail"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic self-test (hermetic — fixture trees written to a tempdir)
# ---------------------------------------------------------------------------

def _write_tree(base, spec):
    for rel, content in spec.items():
        full = os.path.join(base, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)


def run_self_test():
    import tempfile

    # Layered-architecture intent: domain depends on nothing / application depends
    # on domain only / infrastructure may depend on application and domain. Naming:
    # domain files must be *_entity.py, and no *Controller.py may live in domain
    # (controllers belong in infrastructure).
    config = {
        "layers": {
            "domain": {
                "roots": ["src/domain"],
                "import_markers": [r"from +domain", r"import +domain"],
            },
            "application": {
                "roots": ["src/application"],
                "import_markers": [r"from +application", r"import +application"],
            },
            "infrastructure": {
                "roots": ["src/infrastructure"],
                "import_markers": [r"from +infrastructure", r"import +infrastructure"],
            },
        },
        "allowed_dependencies": {
            "domain": [],
            "application": ["domain"],
            "infrastructure": ["application", "domain"],
        },
        "naming": [
            {
                "applies_to": "domain",
                "must_match": r"_entity\.py$",
                "message": "domain files must be named *_entity.py",
            },
            {
                "applies_to": "domain",
                "must_not_match": r"Controller\.py$",
                "message": "no Controller in domain (infrastructure only)",
            },
        ],
    }

    # --- compliant tree (expect zero violations) ---
    compliant = {
        "src/domain/user_entity.py": "class User:\n    pass\n",
        "src/application/user_service.py": "from domain.user_entity import User\n",
        "src/infrastructure/user_repo.py":
            "from application.user_service import svc\nfrom domain.user_entity import User\n",
        "README.md": "docs\n",  # unclassified files are ignored
    }

    # --- violating tree (expect 2 dependency + 2 naming) ---
    violating = {
        # dependency violation 1: domain imports application (domain may depend on nothing)
        "src/domain/user_entity.py": "from application.user_service import svc\n",
        # dependency violation 2: application imports infrastructure (application may depend on domain only)
        "src/application/user_service.py": "from infrastructure.user_repo import repo\n",
        # naming violation 1: a domain file that is not *_entity.py -> must_match miss
        #          (and it is Controller.py, so naming violation 2 = must_not_match hit)
        "src/domain/UserController.py": "x = 1\n",
        # infrastructure may depend on both application + domain -> confirms these are NOT flagged
        "src/infrastructure/user_repo.py":
            "from application.user_service import svc\nfrom domain.user_entity import User\n",
    }

    checks = []
    with tempfile.TemporaryDirectory() as td:
        # case 1: compliant tree -> 0 violations / exit 0 equivalent
        c_dir = os.path.join(td, "compliant")
        _write_tree(c_dir, compliant)
        v_c, inv_c = check_tree(c_dir, config)
        ok1 = (len(v_c) == 0 and inv_c == [])
        checks.append(("compliant -> 0 violations", ok1))

        # case 2: violating tree -> 2 dependency + 2 naming = 4 violations / exit 1 equivalent
        v_dir = os.path.join(td, "violating")
        _write_tree(v_dir, violating)
        v_v, inv_v = check_tree(v_dir, config)
        dep = [v for v in v_v if v["type"] == "dependency"]
        nam = [v for v in v_v if v["type"] == "naming"]
        ok2 = (len(dep) == 2 and len(nam) == 2 and inv_v == [])
        checks.append(("violating -> 2 dep + 2 naming", ok2))

        # case 3: the exact dependency edges (domain->application, application->infrastructure)
        dep_pairs = set()
        for v in dep:
            # extract the leading "src -> target" from the detail
            head = v["detail"].split(" is not")[0]
            dep_pairs.add(head.strip())
        ok3 = dep_pairs == {"domain -> application", "application -> infrastructure"}
        checks.append(("dependency edges exact", ok3))

        # case 4: infrastructure -> {application, domain} is allowed, so it is not flagged
        ok4 = not any(v["file"].startswith("src/infrastructure/") for v in v_v)
        checks.append(("allowed deps not flagged", ok4))

        # case 5: both naming rules (must_match + must_not_match) fire on UserController.py
        ctrl = [v for v in nam if v["file"] == "src/domain/UserController.py"]
        ok5 = (len(ctrl) == 2)
        checks.append(("naming both rules fire", ok5))

        # case 6: exclude works (excluding domain/ drops its dependency + naming violations)
        cfg_ex = dict(config)
        cfg_ex["exclude"] = ["UserController.py"]
        # UserController is a naming-only violation, so verify exclude with a broader path:
        cfg_ex["exclude"] = ["src/domain/"]
        v_ex, _ = check_tree(v_dir, cfg_ex)
        # excluding domain/ removes the domain->application dependency + 2 domain naming violations
        ok6 = (not any(v["file"].startswith("src/domain/") for v in v_ex)
               and any(v["file"] == "src/application/user_service.py" for v in v_ex))
        checks.append(("exclude filters tree", ok6))

        # case 7: an invalid regex is set aside as invalid and does not crash
        cfg_bad = json.loads(json.dumps(config))
        cfg_bad["naming"].append({"applies_to": "*", "must_match": "[unclosed("})
        v_bad, inv_bad = check_tree(c_dir, cfg_bad)
        ok7 = (len(inv_bad) == 1 and len(v_bad) == 0)
        checks.append(("invalid regex graceful", ok7))

    all_ok = True
    for name, ok in checks:
        print("[self-test] %-32s -> %s" % (name, "OK" if ok else "FAIL"))
        all_ok = all_ok and ok

    if all_ok:
        print("[self-test] RESULT: PASS")
        return 0
    print("[self-test] RESULT: FAIL")
    return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_config(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser(
        description="Detect violations of a code tree against a config "
                    "(allowed dependency directions + naming rules): a generic "
                    "architecture parity gate (detection only — fixing is a human's job)."
    )
    ap.add_argument("--config", help="JSON declaring the architectural intent")
    ap.add_argument("--root", help="root directory of the code tree to compare")
    ap.add_argument("--self-test", action="store_true",
                    help="verify the comparison logic against built-in fixtures (hermetic, deterministic --check)")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(run_self_test())

    if not args.config or not args.root:
        sys.stderr.write("usage: --config <config.json> --root <dir> (or --self-test)\n")
        sys.exit(2)

    if not os.path.isdir(args.root):
        sys.stderr.write("root not a directory: %s\n" % args.root)
        sys.exit(2)

    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        sys.stderr.write("config read/parse error: %s\n" % exc)
        sys.exit(2)

    violations, invalid = check_tree(args.root, config)
    print(format_report(args.root, config, violations, invalid))

    # Violations = gate fails (exit 1). Fixing is a human's job in a PR (no self-apply).
    sys.exit(1 if violations else 0)


if __name__ == "__main__":
    main()
