#!/usr/bin/env python3
# =============================================================
# a11y-static-check.py — accessibility static gate (detection only)
#
# Goal: fail a build when a UI change ships an interactive element that a screen
# reader cannot use. The rule set is the "accessible name / role / state" triad
# (WCAG 2.2 SC 4.1.2) applied to the three stacks this kit targets:
#
#   Flutter/Dart  (.dart)                 — Semantics / tooltip / semanticLabel
#   React/Next.js (.tsx .jsx)             — ARIA + semantic HTML
#   WordPress/PHP (.php .html .twig)      — semantic HTML
#
# It parses structure (a paren/tag stack with strings + comments masked), not
# single lines, so "is this tappable thing wrapped by a labelled ancestor?" is
# answered by the actual ancestor chain — the question a line-window grep gets
# wrong in both directions.
#
# Non-goals (safety constraints):
#   - DETECTION ONLY. Never edits code, never adds a Semantics widget, never
#     rewrites a baseline unless --update-baseline is passed explicitly.
#   - Never installs itself into CI. Wiring this as a required status check is a
#     deliberate, higher-risk change a human makes on purpose.
#   - No network, no gh/aws. Local file reads only.
#   - Static analysis cannot prove accessibility. Passing this gate means "no
#     *statically detectable* naming/role defect" — it never means "accessible".
#     Screen-reader traversal and large-text layout still need the runtime tests
#     and the device pass described in skills/a11y-standards/SKILL.md.
#
# Usage:
#   python3 scripts/a11y-static-check.py --root <dir> [options]
#     --root <dir>              tree to scan (required)
#     --baseline <file>         known-violation snapshot (ratchet mode: only NEW findings fail)
#     --update-baseline         rewrite the baseline from the current scan (refuses to grow it)
#     --allow-baseline-growth   permit a baseline with MORE errors than before (must be justified)
#     --strict                  warnings fail too (default: warnings are reported, not fatal)
#     --format text|json        output format (default text)
#     --exclude <substr>        extra path substring to skip (repeatable)
#     --rules <ID,ID,...>       only run these rule ids
#     --self-test               deterministic hermetic self-check
#
# Exit codes:
#   0 = no NEW error-severity findings (with --strict: no new warnings either)
#   1 = new violations found
#   2 = usage / baseline read error (a missing baseline is an error, never a pass)
#   3 = UNKNOWN — zero scannable files. An empty scan is NOT "zero violations";
#       it usually means a wrong --root or an over-broad --exclude.
#
# Design notes:
#   - stdlib only (python3). No jq, no node, no pub.
#   - Deterministic: findings are sorted; the fingerprint used by the ratchet is
#     content-based (rule + file + normalized snippet + ordinal), so unrelated
#     edits above a finding do not churn the baseline.
#   - Suppression needs a reason: `a11y-ignore: <why>` on the finding line or the
#     line above. A bare `a11y-ignore` does not suppress (it is reported).
# =============================================================

import argparse
import hashlib
import json
import os
import re
import sys

SCHEMA_VERSION = 1

ERROR = "error"
WARN = "warn"

# ---------------------------------------------------------------------------
# File selection
# ---------------------------------------------------------------------------

DART_EXT = (".dart",)
WEB_EXT = (".tsx", ".jsx")
MARKUP_EXT = (".php", ".html", ".htm", ".twig")

SCAN_EXT = DART_EXT + WEB_EXT + MARKUP_EXT

# Directories/paths never scanned: vendored, generated, or test code. Test code is
# excluded because fixtures deliberately build bare widgets; enforcing product
# a11y rules there produces noise, not safety.
DEFAULT_EXCLUDES = [
    "/.git/",
    "/node_modules/",
    "/vendor/",
    "/build/",
    "/.next/",
    "/.dart_tool/",
    "/dist/",
    "/coverage/",
    "/ios/Pods/",
    "/test/",
    "/tests/",
    "/spec/",
    "/__tests__/",
    "/integration_test/",
    "/e2e/",
    "/.storybook/",
    "/playwright-report/",
    "/storybook-static/",
    "/.output/",
    ".g.dart",
    ".freezed.dart",
    ".gr.dart",
    ".mocks.dart",
    ".config.dart",
    ".test.tsx",
    ".test.jsx",
    ".spec.tsx",
    ".spec.jsx",
    ".stories.tsx",
    ".stories.jsx",
    ".min.js",
]

IGNORE_RE = re.compile(r"a11y-ignore\s*:\s*(\S.*)$")
IGNORE_BARE_RE = re.compile(r"a11y-ignore(?!\s*:)")


def iter_files(root, excludes):
    """Every scannable file under root, as (relpath, fullpath), sorted."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", ".dart_tool")]
        for fn in sorted(filenames):
            if not fn.endswith(SCAN_EXT):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            probe = "/" + rel
            if any(ex and ex in probe for ex in excludes):
                continue
            out.append((rel, full))
    out.sort()
    return out


def read_text(full):
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Shared helpers: labels, symbols, offsets
# ---------------------------------------------------------------------------

# A label is "symbol-only" when it carries no letter, digit or CJK character —
# e.g. "<" (a screen reader says "less than"), "×", "•••", "+", "❯".
# NOTE: the Latin-1 supplement range deliberately skips U+00D7 (multiplication sign)
# and U+00F7 (division sign): they sit between accented letters but are math symbols,
# and U+00D7 is the most common "close button" glyph. Treating it as a letter would
# silently pass exactly the case this rule exists to catch.
WORDISH_RE = re.compile(
    r"[0-9A-Za-z"
    r"\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f"
    r"\u0400-\u04ff\u3040-\u309f\u30a0-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]"
)

UNICODE_ESCAPE_RE = re.compile(r"\\u\{([0-9a-fA-F]+)\}|\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})")


def decode_escapes(s):
    """Decode \\uXXXX / \\u{...} / \\xXX so '\\u2022\\u2022\\u2022' is seen as '•••'."""

    def sub(m):
        hexpart = m.group(1) or m.group(2) or m.group(3)
        try:
            return chr(int(hexpart, 16))
        except (ValueError, OverflowError):
            return ""

    return UNICODE_ESCAPE_RE.sub(sub, s)


def is_symbol_only(label):
    return not WORDISH_RE.search(decode_escapes(label))


def line_of(text, offset):
    return text.count("\n", 0, offset) + 1


def normalize_snippet(s):
    """Whitespace-insensitive snippet used for the content-based fingerprint."""
    return re.sub(r"\s+", " ", s).strip()[:240]


def mask_regions(text, regions, keep_newlines=True):
    """Replace each (start, end) region with spaces, preserving offsets (and line
    numbers, when keep_newlines)."""
    buf = list(text)
    for start, end in regions:
        for i in range(start, min(end, len(buf))):
            if keep_newlines and buf[i] == "\n":
                continue
            buf[i] = " "
    return "".join(buf)


# ---------------------------------------------------------------------------
# Dart / Flutter
# ---------------------------------------------------------------------------

def dart_masked(text):
    """Mask comments and string *contents* (quotes kept) so a paren scan is safe."""
    regions = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            regions.append((i, j))
            i = j
            continue
        if c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            regions.append((i, j))
            i = j
            continue
        if c in "'\"":
            triple = text[i:i + 3]
            if triple in ("'''", '"""'):
                quote = triple
                j = text.find(quote, i + 3)
                j = n if j < 0 else j + 3
                regions.append((i + 3, j - 3 if j > i + 3 else j))
                i = j
                continue
            quote = c
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == quote:
                    break
                if text[j] == "\n":  # unterminated single-line string: stop here
                    break
                j += 1
            regions.append((i + 1, j))
            i = min(j + 1, n)
            continue
        i += 1
    return mask_regions(text, regions)


IDENT_BEFORE_PAREN_RE = re.compile(r"([A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)\s*$")


class Node(object):
    __slots__ = ("name", "start", "open", "end", "line", "parent", "children")

    def __init__(self, name, start, open_pos, line, parent):
        self.name = name
        self.start = start          # start of the identifier
        self.open = open_pos        # position of "("
        self.end = -1               # position just after the matching ")"
        self.line = line
        self.parent = parent
        self.children = []


def parse_call_tree(text, masked):
    """Build the constructor-call tree of a Dart file.

    Every "(" is tracked so paren matching stays correct, but only capitalized
    identifiers (widget/class constructors) become tree nodes, which is what the
    ancestor questions are about.
    """
    nodes = []
    stack = []          # every open paren: (node_or_None,)
    named_stack = []    # only named nodes
    i = 0
    n = len(masked)
    while i < n:
        c = masked[i]
        if c == "(":
            m = IDENT_BEFORE_PAREN_RE.search(masked, 0, i)
            name = m.group(1) if m else None
            node = None
            if name and name.split(".")[0][:1].isupper():
                parent = named_stack[-1] if named_stack else None
                node = Node(name, m.start(1), i, line_of(text, m.start(1)), parent)
                if parent is not None:
                    parent.children.append(node)
                nodes.append(node)
                named_stack.append(node)
            stack.append(node)
        elif c == ")":
            if stack:
                node = stack.pop()
                if node is not None:
                    node.end = i + 1
                    if named_stack and named_stack[-1] is node:
                        named_stack.pop()
        i += 1
    # Unterminated nodes (truncated file) end at EOF so slicing stays safe.
    for nd in nodes:
        if nd.end < 0:
            nd.end = n
    return nodes


def own_text(text, node):
    """The node's own argument text, with nested named-node spans removed, so
    `onTap:` found here belongs to THIS widget and not to a descendant."""
    pieces = []
    cursor = node.open
    for child in node.children:
        if child.start < cursor:
            continue
        pieces.append(text[cursor:child.start])
        cursor = max(cursor, child.end)
    pieces.append(text[cursor:node.end])
    return "".join(pieces)


DART_STRING_RE = re.compile(r"'''(.*?)'''|\"\"\"(.*?)\"\"\"|'((?:\\.|[^'\\\n])*)'|\"((?:\\.|[^\"\\\n])*)\"", re.S)


def first_string_literal(s):
    m = DART_STRING_RE.search(s)
    if not m:
        return None
    for g in m.groups():
        if g is not None:
            return g
    return ""


def _find_top_level_key(own, key):
    """Position just after `key:` when it appears at the node's OWN argument depth.

    Depth matters: `Column(children: [ ... onTap: () => x ... ])` must not make the
    Column itself look tappable just because a closure inside its child list has a
    tap handler. `own` starts at the node's own "(", and that paren establishes the
    argument level, so scanning starts just inside it.
    """
    pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(key) + r"\s*:")
    depth = 0
    i = 1 if own[:1] == "(" else 0
    n = len(own)
    while i < n:
        c = own[i]
        if depth == 0:
            m = pat.match(own, i)
            if m:
                return m.end()
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break  # this closes the node's own argument list
            depth -= 1
        i += 1
    return None


def arg_value(own, key):
    """Raw text of `key:` up to the next top-level comma (approximate but enough
    to tell null / '' / an expression apart)."""
    end = _find_top_level_key(own, key)
    if end is None:
        return None
    i = end
    depth = 0
    out = []
    while i < len(own):
        ch = own[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            break
        out.append(ch)
        i += 1
    return "".join(out).strip()


def has_nonempty_arg(own, key):
    v = arg_value(own, key)
    if v is None:
        return False
    if v in ("null", ""):
        return False
    lit = first_string_literal(v)
    if lit is not None and lit.strip() == "" and v.strip().startswith(("'", '"')):
        return False  # hintText: '' names nothing
    return True


# Custom tap wrappers: no intrinsic role — a screen reader announces the child,
# never "button", unless Semantics(button: true) is added.
DART_CUSTOM_TAP = {"GestureDetector", "InkWell", "InkResponse", "RawGestureDetector"}

# Real button/interactive widgets: role comes from the framework, name does not.
DART_NATIVE_INTERACTIVE = {
    "IconButton", "TextButton", "ElevatedButton", "OutlinedButton", "FilledButton",
    "CupertinoButton", "FloatingActionButton", "PopupMenuButton", "ListTile",
    "CheckboxListTile", "SwitchListTile", "RadioListTile", "MenuItemButton",
    "SegmentedButton", "DropdownButton", "Chip", "ActionChip", "InputChip",
    "CloseButton", "BackButton",
}

DART_TAP_ARGS = ("onTap", "onPressed", "onLongPress", "onDoubleTap", "onTapDown", "onSelected")

# Layout/scroll containers: they take builder callbacks that legitimately contain tap
# handlers deeper down, so they must never be classified as the tappable element.
DART_CONTAINER_NAMES = {
    "Column", "Row", "Stack", "Padding", "Center", "Container", "SizedBox", "Expanded",
    "Flexible", "Wrap", "ListView", "GridView", "PageView", "CustomScrollView",
    "SingleChildScrollView", "Scaffold", "MaterialApp", "CupertinoApp", "Builder",
    "AnimatedBuilder", "LayoutBuilder", "ValueListenableBuilder", "Consumer",
}

DART_FIELDS = {"TextField", "TextFormField", "CupertinoTextField", "CupertinoSearchTextField"}
DART_TOGGLES = {"Switch", "CupertinoSwitch", "Checkbox", "Radio", "Slider", "SwitchListTile", "CheckboxListTile"}
DART_IMAGES = {"Image", "Image.asset", "Image.network", "Image.file", "Image.memory", "FadeInImage"}

# Widgets that give their child an accessible name.
DART_NAMING_ANCESTORS = {"Semantics": ("label",), "Tooltip": ("message",), "MergeSemantics": ()}
DART_LABEL_CARRIERS = {"ListTile", "CheckboxListTile", "SwitchListTile", "RadioListTile"}


def dart_label_state(text, node):
    """Resolve the accessible name/role/state situation for one node.

    Returns (named, symbol_label, role_ok, excluded, enabled_declared) where
    `symbol_label` is set only when the *effective* name is symbol-only. An
    explicit Semantics/tooltip label wins over descendant text (that is the
    precedence Flutter applies), so a symbol glyph deliberately hidden under
    ExcludeSemantics next to a real label is not a finding.
    """
    explicit_label = None      # from Semantics(label:) / tooltip: / semanticLabel:
    explicit_named = False     # an explicit name exists (possibly a non-literal expression)
    text_label = None          # first literal from a descendant Text()
    text_named = False
    role_ok = node.name in DART_NATIVE_INTERACTIVE
    excluded = False
    enabled_declared = False

    own = own_text(text, node)

    def note_explicit(raw):
        nonlocal explicit_label, explicit_named
        explicit_named = True
        lit = first_string_literal(raw or "")
        if explicit_label is None and lit is not None and lit.strip():
            explicit_label = lit

    # self args
    for key in ("tooltip", "semanticLabel"):
        if has_nonempty_arg(own, key):
            note_explicit(arg_value(own, key))
    if node.name in DART_LABEL_CARRIERS | {"Semantics"} and has_nonempty_arg(own, "label"):
        note_explicit(arg_value(own, "label"))
    if node.name in DART_LABEL_CARRIERS and has_nonempty_arg(own, "title"):
        explicit_named = True

    # ancestors
    cur = node.parent
    while cur is not None:
        if cur.name == "ExcludeSemantics":
            excluded = True
        a_own = own_text(text, cur)
        if cur.name == "Semantics":
            if has_nonempty_arg(a_own, "label"):
                note_explicit(arg_value(a_own, "label"))
            if re.search(r"(?<![A-Za-z0-9_])button\s*:\s*true", a_own):
                role_ok = True
            if re.search(r"(?<![A-Za-z0-9_])enabled\s*:", a_own):
                enabled_declared = True
        elif cur.name == "Tooltip" and has_nonempty_arg(a_own, "message"):
            note_explicit(arg_value(a_own, "message"))
        elif cur.name in DART_LABEL_CARRIERS and (
            has_nonempty_arg(a_own, "title") or has_nonempty_arg(a_own, "label")
        ):
            explicit_named = True
            role_ok = True
        cur = cur.parent

    # descendants: a Text/Icon child names the element (Flutter derives the
    # semantic label from text automatically). Subtrees explicitly hidden with
    # ExcludeSemantics are skipped — they contribute no name.
    def walk(nd):
        for ch in nd.children:
            if ch.name == "ExcludeSemantics":
                continue
            yield ch
            for sub in walk(ch):
                yield sub

    for d in walk(node):
        if d.name in ("Text", "SelectableText", "RichText"):
            lit = first_string_literal(own_text(text, d))
            if lit is None:
                text_named = True  # Text(variable) — dynamic but a name exists
            elif lit.strip() == "":
                continue
            elif is_symbol_only(lit):
                text_named = True
                if text_label is None:
                    text_label = lit
            else:
                text_named = True
                text_label = None
                break
        elif d.name == "Icon" and has_nonempty_arg(own_text(text, d), "semanticLabel"):
            note_explicit(arg_value(own_text(text, d), "semanticLabel"))
        elif d.name == "Semantics" and has_nonempty_arg(own_text(text, d), "label"):
            note_explicit(arg_value(own_text(text, d), "label"))

    named = explicit_named or text_named
    # Effective name: an explicit label wins; only fall back to descendant text.
    if explicit_named:
        symbol_label = explicit_label if (explicit_label and is_symbol_only(explicit_label)) else None
    else:
        symbol_label = text_label
    return named, symbol_label, role_ok, excluded, enabled_declared


def scan_dart(rel, text, emit):
    masked = dart_masked(text)
    nodes = parse_call_tree(text, masked)
    interactive_seen = 0
    unknown_ctors = {}

    for node in nodes:
        own = own_text(text, node)
        # Named constructors (`TextButton.icon`, `FloatingActionButton.extended`,
        # `ListView.builder`) must classify like their base widget, or every `.icon`
        # variant looks like an unknown project widget.
        base_name = node.name.split(".")[0]
        is_custom = base_name in DART_CUSTOM_TAP
        is_native = base_name in DART_NATIVE_INTERACTIVE
        tap_arg = None
        conditional_tap = False
        for key in DART_TAP_ARGS:
            v = arg_value(own, key)
            if v is None:
                continue
            if v == "null":
                continue
            tap_arg = key
            if "?" in v and ":" in v:
                conditional_tap = True
            break

        if (is_custom or is_native) and tap_arg:
            interactive_seen += 1
            named, symbol_label, role_ok, excluded, enabled_declared = dart_label_state(text, node)
            if excluded:
                continue
            snippet = normalize_snippet(text[node.start:min(node.end, node.start + 200)])
            if not named:
                emit(
                    rule="A11Y-FLUTTER-NAME",
                    severity=ERROR,
                    file=rel,
                    line=node.line,
                    message="%s に %s があるが accessible name が無い（読み上げでは無名の要素になる）" % (node.name, tap_arg),
                    fix="Semantics(button: true, label: '<操作の意味>', child: ...) で包む / IconButton なら tooltip: を付ける",
                    snippet=snippet,
                )
            elif symbol_label:
                emit(
                    rule="A11Y-FLUTTER-SYMBOL-LABEL",
                    severity=ERROR,
                    file=rel,
                    line=node.line,
                    message="操作要素のラベルが記号のみ（%s）— 記号名で読み上げられる（例: '<' は「小なり」）"
                            % repr(decode_escapes(symbol_label)),
                    fix="Semantics(label: '戻る') 等の意味のあるラベルを付け、記号は excludeSemantics 側へ",
                    snippet=snippet,
                )
            elif is_custom and not role_ok:
                emit(
                    rule="A11Y-FLUTTER-ROLE",
                    severity=ERROR,
                    file=rel,
                    line=node.line,
                    message="%s にラベルはあるが button role が無い（「ボタン」と読み上げられず操作可能だと伝わらない）" % node.name,
                    fix="Semantics(button: true, child: ...) を付ける / 実ボタン Widget（TextButton 等）に置き換える",
                    snippet=snippet,
                )
            if conditional_tap and not enabled_declared:
                emit(
                    rule="A11Y-FLUTTER-STATE",
                    severity=WARN,
                    file=rel,
                    line=node.line,
                    message="%s が %s で有効/無効を切り替えているが Semantics(enabled:) が無い（無効状態が読み上げられない）" % (node.name, tap_arg),
                    fix="Semantics(button: true, enabled: <条件>, label: ...) で状態も公開する",
                    snippet=snippet,
                )

        # The interactive-constructor lists above are hardcoded, so they drift as the
        # framework and the project's own widgets evolve. Report tappables we could not
        # classify instead of silently passing them: a filter nobody can see is worse
        # than a finding nobody expected.
        if (
            tap_arg
            and not is_custom
            and not is_native
            and base_name not in DART_CONTAINER_NAMES
            and node.name[:1].isupper()
        ):
            # Aggregate per constructor: 39 identical lines about one shared wrapper is
            # noise, "this wrapper is used 39 times and we cannot see inside it" is signal.
            entry = unknown_ctors.setdefault(node.name, {"line": node.line, "count": 0, "arg": tap_arg})
            entry["count"] += 1
            entry["line"] = min(entry["line"], node.line)

        if base_name in DART_FIELDS:
            named, symbol_label, _role, excluded, _en = dart_label_state(text, node)
            deco = None
            for ch in node.children:
                if ch.name in ("InputDecoration", "BoxDecoration"):
                    deco = own_text(text, ch)
                    break
            labelled = (
                has_nonempty_arg(own, "labelText")
                or has_nonempty_arg(own, "placeholder")
                or (deco is not None and (has_nonempty_arg(deco, "labelText") or has_nonempty_arg(deco, "hintText")))
            )
            if not excluded and not labelled and not named:
                emit(
                    rule="A11Y-FLUTTER-FIELD-NAME",
                    severity=ERROR,
                    file=rel,
                    line=node.line,
                    message="%s に accessible name が無い（無名の入力欄として読み上げられる）" % node.name,
                    fix="InputDecoration(labelText: '<項目名>') / Semantics(textField: true, label: '<項目名>')",
                    snippet=normalize_snippet(text[node.start:min(node.end, node.start + 200)]),
                )

        if base_name in DART_TOGGLES:
            named, _sym, _role, excluded, _en = dart_label_state(text, node)
            if not excluded and not named:
                emit(
                    rule="A11Y-FLUTTER-TOGGLE-NAME",
                    severity=ERROR,
                    file=rel,
                    line=node.line,
                    message="%s に accessible name が無い（何のスイッチか読み上げられない）" % node.name,
                    fix="Semantics(label: '<対象>', child: ...) で包む / SwitchListTile(title: Text('<対象>')) を使う",
                    snippet=normalize_snippet(text[node.start:min(node.end, node.start + 200)]),
                )

        # A global text-scale clamp below 200% turns "we support large text" into a
        # violation of the very criterion it claims to satisfy (WCAG 1.4.4 asks for
        # 200%), and it also disqualifies Apple's "Larger Text" declaration.
        if node.name in ("MediaQuery.withClampedTextScaling",) or (
            node.name == "TextScaler.clamp"
        ):
            raw_max = arg_value(own, "maxScaleFactor")
            if raw_max:
                m_num = re.search(r"([0-9]+(?:\.[0-9]+)?)", raw_max)
                if m_num:
                    try:
                        if float(m_num.group(1)) < 2.0:
                            emit(
                                rule="A11Y-FLUTTER-SCALE-CLAMP",
                                severity=ERROR,
                                file=rel,
                                line=node.line,
                                message="文字拡大を %s 倍でクランプしている — 200%% までの拡大を要求する WCAG 1.4.4 に反し、"
                                        "Apple の Larger Text 申告も満たせない" % m_num.group(1),
                                fix="maxScaleFactor は 2.0 以上にする。溢れるなら固定高さを可変にする（クランプは緩和策で修正ではない）",
                                snippet=normalize_snippet(text[node.start:min(node.end, node.start + 160)]),
                            )
                    except ValueError:
                        pass

        if base_name in DART_IMAGES or node.name in DART_IMAGES:
            inside_interactive = False
            cur = node.parent
            while cur is not None:
                if cur.name in DART_CUSTOM_TAP or cur.name in DART_NATIVE_INTERACTIVE:
                    inside_interactive = True
                    break
                cur = cur.parent
            excl = re.search(r"excludeFromSemantics\s*:\s*true", own) is not None
            if not inside_interactive and not excl and not has_nonempty_arg(own, "semanticLabel"):
                emit(
                    rule="A11Y-FLUTTER-IMAGE-LABEL",
                    severity=WARN,
                    file=rel,
                    line=node.line,
                    message="%s に semanticLabel が無い（情報を持つ画像なら読み上げ不能・装飾なら明示が必要）" % node.name,
                    fix="意味のある画像は semanticLabel: '<内容>' / 装飾は excludeFromSemantics: true",
                    snippet=normalize_snippet(text[node.start:min(node.end, node.start + 160)]),
                )

    for ctor, info in sorted(unknown_ctors.items()):
        emit(
            rule="A11Y-FLUTTER-UNKNOWN-TAP-CTOR",
            severity=WARN,
            file=rel,
            line=info["line"],
            message="%s（%s を持つ・このファイルで %d 箇所）は既知の操作要素リストに無い — "
                    "name/role を検査できていない" % (ctor, info["arg"], info["count"]),
            fix="共通ボタン Widget ならその定義ファイル側で name/role を保証する（1 箇所直せば全呼出に効く）",
            snippet="unknown-tap-ctor:%s" % ctor,
        )

    return {"interactive": interactive_seen}


# ---------------------------------------------------------------------------
# Markup (JSX/TSX + PHP/HTML)
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9_.:-]*)((?:[^<>\"'{}]|\"[^\"]*\"|'[^']*'|\{[^{}]*\})*)(/?)>", re.S)
ATTR_RE = re.compile(r"([A-Za-z_:][-A-Za-z0-9_:.]*)\s*(?:=\s*(\"[^\"]*\"|'[^']*'|\{[^{}]*\}|[^\s>]+))?")

WEB_NONINTERACTIVE = {"div", "span", "li", "td", "tr", "p", "section", "article", "header", "footer", "main", "nav", "figure", "label", "h1", "h2", "h3", "h4", "h5", "h6"}
WEB_CLICK_ATTRS = ("onClick", "onclick", "onMouseDown", "onMouseUp", "onPointerDown")
WEB_KEY_ATTRS = ("onKeyDown", "onKeyUp", "onKeyPress", "onkeydown")
WEB_CONTROLS = {"button", "a", "summary"}
WEB_FIELDS = {"input", "select", "textarea"}
WEB_NAME_ATTRS = ("aria-label", "aria-labelledby", "title", "alt")


def markup_masked(text, php=False):
    """Mask comments (JS/JSX/HTML), template literals and, for PHP, the inside of
    <?php ?> blocks — keeping offsets so line numbers stay true.

    Template literals matter: docs pages embed JSX *as a code sample* inside
    backticks. Scanning those produces findings about text that never renders,
    which is the fastest way to make a gate look untrustworthy.
    """
    regions = []
    for m in re.finditer(r"<!--.*?-->", text, re.S):
        regions.append((m.start(), m.end()))
    for m in re.finditer(r"/\*.*?\*/", text, re.S):
        regions.append((m.start(), m.end()))
    if not php:
        for m in re.finditer(r"`(?:\\.|[^`\\])*`", text, re.S):
            regions.append((m.start() + 1, m.end() - 1))
    if php:
        # Keep a marker so "there is dynamic output here" is still detectable:
        # replace only the code body, not the delimiters.
        for m in re.finditer(r"<\?(?:php|=)?(.*?)(?:\?>|$)", text, re.S):
            if m.group(1):
                regions.append((m.start(1), m.end(1)))
    return mask_regions(text, regions)


def parse_attrs(raw):
    attrs = {}
    for m in ATTR_RE.finditer(raw or ""):
        key = m.group(1)
        val = m.group(2)
        if val is None:
            attrs[key] = True
            continue
        if val[:1] in "\"'" and val[-1:] == val[:1]:
            attrs[key] = val[1:-1]
        else:
            attrs[key] = val  # {expr} or bare
    return attrs


def attr_names(attrs, keys):
    for k in keys:
        if k in attrs:
            v = attrs[k]
            if v is True:
                continue
            if isinstance(v, str):
                s = v.strip()
                if s == "" or s in ("{''}", '{""}', "{}"):
                    continue
                if s.startswith("{") and s.endswith("}"):
                    return True  # {t('save')} — dynamic name, assume real
                return True
    return False


def scan_markup(rel, text, php, emit):
    masked = markup_masked(text, php=php)
    tags = []
    for m in TAG_RE.finditer(masked):
        closing = m.group(1) == "/"
        name = m.group(2)
        selfclose = m.group(4) == "/" or name.lower() in ("img", "input", "br", "hr", "source", "meta", "link")
        tags.append({
            "closing": closing,
            "name": name,
            "lname": name.lower(),
            "raw": text[m.start(3):m.end(3)],
            "start": m.start(),
            "end": m.end(),
            "selfclose": selfclose,
            "line": line_of(text, m.start()),
        })

    # Pair open/close to know inner text
    stack = []
    interactive_seen = 0
    for idx, t in enumerate(tags):
        if t["closing"]:
            for j in range(len(stack) - 1, -1, -1):
                if stack[j]["lname"] == t["lname"]:
                    stack[j]["inner_end"] = t["start"]
                    del stack[j:]
                    break
            continue
        t["inner_end"] = None
        if not t["selfclose"]:
            stack.append(t)

    def inner_text(t):
        if t.get("inner_end"):
            return text[t["end"]:t["inner_end"]]
        return ""

    # <label> association, both forms the HTML spec allows:
    #   explicit — <label for="x"> ... <input id="x">
    #   implicit — <label>Campaign ID <input></label>  (no id needed, and valid)
    # Missing the implicit form is a false positive on perfectly accessible code.
    label_for_ids = set()
    label_spans = []
    for t in tags:
        if t["closing"] or t["lname"] != "label":
            continue
        lattrs = parse_attrs(t["raw"])
        for key in ("for", "htmlFor"):
            v = lattrs.get(key)
            if isinstance(v, str) and v.strip():
                label_for_ids.add(v.strip().strip("{}\"'"))
        if t.get("inner_end"):
            body = text[t["end"]:t["inner_end"]]
            named_label = bool(WORDISH_RE.search(re.sub(r"<[^>]*>", " ", body))) or ("{" in body) or ("<?" in body)
            label_spans.append((t["end"], t["inner_end"], named_label))

    def wrapped_by_named_label(pos):
        return any(start <= pos <= end and named for start, end, named in label_spans)

    for t in tags:
        if t["closing"]:
            continue
        attrs = parse_attrs(t["raw"])
        lname = t["lname"]
        snippet = normalize_snippet(text[t["start"]:t["end"]])
        prefix = "A11Y-MARKUP" if php else "A11Y-WEB"

        # A zoom-locked viewport breaks WCAG 1.4.4 for everyone on the page at once,
        # and it is one grep away from certainty — no ancestor reasoning needed.
        if lname == "meta" and str(attrs.get("name", "")).strip().strip("\"'") == "viewport":
            content = str(attrs.get("content", ""))
            zoom_locked = re.search(r"user-scalable\s*=\s*(no|0)", content) is not None
            max_scale = re.search(r"maximum-scale\s*=\s*([0-9.]+)", content)
            if max_scale:
                try:
                    zoom_locked = zoom_locked or float(max_scale.group(1)) < 2.0
                except ValueError:
                    pass
            if zoom_locked:
                emit(
                    rule=prefix + "-VIEWPORT-ZOOM",
                    severity=ERROR,
                    file=rel,
                    line=t["line"],
                    message="viewport が拡大を禁止している（user-scalable=no / maximum-scale<2）— 拡大が必要な利用者を全ページで排除する",
                    fix='content="width=device-width, initial-scale=1" のみにする（拡大禁止を外す）',
                    snippet=snippet,
                )

        # A positive tabindex rewrites the tab order globally; 0 / -1 are the only
        # values that stay in document order.
        tabindex_val = attrs.get("tabindex", attrs.get("tabIndex"))
        if isinstance(tabindex_val, str):
            # The sign must be part of the match: a bare [0-9]+ reads "-1" as 1.
            m_ti = re.search(r"(-?[0-9]+)", tabindex_val)
            if m_ti and int(m_ti.group(1)) > 0:
                emit(
                    rule=prefix + "-POSITIVE-TABINDEX",
                    severity=ERROR,
                    file=rel,
                    line=t["line"],
                    message="tabindex=%s（正の値）— タブ順が DOM 順から乖離し、以降の要素の順序が壊れる" % m_ti.group(1),
                    fix="tabindex は 0（順序に含める）か -1（プログラム的にのみ focus）だけを使い、順序は DOM 側で表現する",
                    snippet=snippet,
                )

        # img / next-Image alt
        if lname in ("img", "image"):
            if "alt" not in attrs:
                emit(
                    rule=prefix + "-IMG-ALT",
                    severity=ERROR,
                    file=rel,
                    line=t["line"],
                    message="<%s> に alt が無い（読み上げでファイル名が読まれる/無視される）" % t["name"],
                    fix='意味のある画像は alt="内容の説明"、装飾は alt=""（空文字を明示）',
                    snippet=snippet,
                )
            continue

        # div/span with a click handler = a button without a role
        if lname in WEB_NONINTERACTIVE and any(k in attrs for k in WEB_CLICK_ATTRS):
            missing = []
            if "role" not in attrs:
                missing.append("role")
            if "tabIndex" not in attrs and "tabindex" not in attrs:
                missing.append("tabIndex")
            if not any(k in attrs for k in WEB_KEY_ATTRS):
                missing.append("キーボードハンドラ")
            interactive_seen += 1
            if missing:
                emit(
                    rule=prefix + "-INTERACTIVE-DIV",
                    severity=ERROR,
                    file=rel,
                    line=t["line"],
                    message="<%s> にクリックハンドラがあるが %s が無い（キーボード/支援技術から操作不能）" % (t["name"], " / ".join(missing)),
                    fix="<button type=\"button\"> に置き換える（推奨）。やむを得ない場合は role/tabIndex/onKeyDown を揃える",
                    snippet=snippet,
                )

        # button / a / summary need an accessible name
        if lname in WEB_CONTROLS:
            interactive_seen += 1
            body = inner_text(t)
            body_text = re.sub(r"<[^>]*>", " ", body).strip()
            body_wordish = bool(WORDISH_RE.search(body_text))
            dynamic_body = ("{" in body and "}" in body) or ("<?" in body)
            child_named = re.search(r"(aria-label|alt)\s*=", body) is not None
            explicit_name = attr_names(attrs, WEB_NAME_ATTRS)
            named = explicit_name or body_wordish or dynamic_body or child_named
            symbol_only_body = bool(body_text) and is_symbol_only(body_text)
            if named:
                pass  # an accessible name exists (explicit attr, text, or dynamic expression)
            elif symbol_only_body:
                # The glyph IS the label — more specific (and more actionable) than "no name".
                emit(
                    rule=prefix + "-SYMBOL-LABEL",
                    severity=ERROR,
                    file=rel,
                    line=t["line"],
                    message="<%s> のラベルが記号のみ（%r）— 読み上げが記号名になる" % (t["name"], body_text[:12]),
                    fix='aria-label="閉じる" 等を付け、記号側は aria-hidden="true"',
                    snippet=snippet,
                )
            else:
                emit(
                    rule=prefix + "-CONTROL-NAME",
                    severity=ERROR,
                    file=rel,
                    line=t["line"],
                    message="<%s> に accessible name が無い（アイコンのみ等・読み上げで用途が伝わらない）" % t["name"],
                    fix='aria-label="操作の意味" を付ける（アイコンには aria-hidden="true"）',
                    snippet=snippet,
                )

        # form controls need a name
        if lname in WEB_FIELDS:
            typ = attrs.get("type")
            if isinstance(typ, str) and typ.strip().strip("\"'") in ("hidden", "submit", "button", "image"):
                continue
            has_name = attr_names(attrs, ("aria-label", "aria-labelledby", "title"))
            raw_id = attrs.get("id")
            id_val = raw_id.strip().strip("{}\"'") if isinstance(raw_id, str) else ""
            has_id = bool(id_val)
            label_matched = (has_id and id_val in label_for_ids) or wrapped_by_named_label(t["start"])
            if label_matched:
                continue
            if not has_name and not has_id:
                emit(
                    rule=prefix + "-FIELD-NAME",
                    severity=ERROR,
                    file=rel,
                    line=t["line"],
                    message="<%s> に accessible name が無い（label 関連付けも aria-label も無い）" % t["name"],
                    fix='<label for="x"> と id="x" を対応させる / aria-label="項目名"',
                    snippet=snippet,
                )
            elif not has_name and has_id:
                emit(
                    rule=prefix + "-FIELD-LABEL-UNVERIFIED",
                    severity=WARN,
                    file=rel,
                    line=t["line"],
                    message="<%s> は id はあるが対応する <label for> を静的に確認できない" % t["name"],
                    fix="同一テンプレート内に <label for> があるか確認（無ければ aria-label を付ける）",
                    snippet=snippet,
                )

    return {"interactive": interactive_seen}


# ---------------------------------------------------------------------------
# Project-level checks (a11y regression harness must exist)
# ---------------------------------------------------------------------------

def scan_project(root, emit, files):
    """Presence checks that make a11y a *standing* property of the repo, not a
    one-off fix: a large-text regression test for Flutter, an a11y linter for web."""
    has_pubspec = os.path.isfile(os.path.join(root, "pubspec.yaml"))
    has_pkg = os.path.isfile(os.path.join(root, "package.json"))

    if has_pubspec:
        found = False
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", ".dart_tool", "build")]
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            if not (rel_dir.startswith("test") or rel_dir.startswith("integration_test") or "/test" in "/" + rel_dir):
                continue
            for fn in filenames:
                if not fn.endswith(".dart"):
                    continue
                body = read_text(os.path.join(dirpath, fn)) or ""
                # `textScaleFactor` is deprecated and must not count as "we test large
                # text" — accepting it would bless the API that is going away.
                if re.search(r"TextScaler|textScalerOf|textScaler\s*:", body):
                    found = True
                    break
            if found:
                break
        if not found:
            emit(
                rule="A11Y-PROJ-SCALE-TEST",
                severity=WARN,
                file="pubspec.yaml",
                line=1,
                message="最大文字サイズ（Dynamic Type）を再現する自動テストが見つからない（拡大時のレイアウト崩れが回帰検知されない）",
                fix="examples/a11y/flutter/a11y_smoke_test.dart を test/ にコピーして配線する",
                snippet="pubspec.yaml",
            )

    # A WordPress theme with a build-tooling package.json is not a React app; asking
    # it for a JSX linter is noise. Only projects that actually ship .tsx/.jsx qualify.
    has_react_files = any(rel.endswith(WEB_EXT) for rel, _full in files)

    if has_pkg and has_react_files:
        # Monorepos keep the lint config in apps/*/ or packages/*/, so a root-only
        # look would report "no a11y linter" on a repo that has one per workspace.
        blob_parts = []
        search_dirs = [root]
        for parent in ("apps", "packages", "web", "frontend"):
            pdir = os.path.join(root, parent)
            if os.path.isdir(pdir):
                for child in sorted(os.listdir(pdir))[:20]:
                    cdir = os.path.join(pdir, child)
                    if os.path.isdir(cdir):
                        search_dirs.append(cdir)
        for d in search_dirs:
            try:
                entries = os.listdir(d)
            except OSError:
                continue
            for f in entries:
                if f == "package.json" or f.startswith(".eslintrc") or f.startswith("eslint.config."):
                    blob_parts.append(read_text(os.path.join(d, f)) or "")
        blob = "".join(blob_parts)
        has_jsx_a11y = "jsx-a11y" in blob
        has_next_preset = "eslint-config-next" in blob or "next/core-web-vitals" in blob
        if not has_jsx_a11y and not has_next_preset:
            emit(
                rule="A11Y-PROJ-WEB-LINT",
                severity=WARN,
                file="package.json",
                line=1,
                message="a11y linter（eslint-plugin-jsx-a11y 等）が設定に見つからない（ARIA/alt/キーボードの回帰が検知されない）",
                fix="examples/a11y/web/eslint-a11y.config.md の設定を追加する",
                snippet="package.json",
            )
        elif not has_jsx_a11y and has_next_preset:
            # eslint-config-next bundles jsx-a11y but enables only a small subset, so
            # "we use next lint" is not the same as "a11y is linted".
            emit(
                rule="A11Y-PROJ-WEB-LINT-SUBSET",
                severity=WARN,
                file="package.json",
                line=1,
                message="a11y lint が Next.js 既定の一部ルールのみ（jsx-a11y の recommended/strict が未適用）",
                fix="jsx-a11y の recommended を明示的に有効化し、severity を error にする（examples/a11y/web/eslint-a11y.config.md）",
                snippet="package.json",
            )


# ---------------------------------------------------------------------------
# Scan driver
# ---------------------------------------------------------------------------

def scan_tree(root, excludes=None, rules=None, project_checks=True):
    """Returns (findings, stats). findings are dicts; stats carries denominators."""
    excludes = list(DEFAULT_EXCLUDES) + list(excludes or [])
    files = iter_files(root, excludes)

    findings = []
    ignored = 0
    bare_pragma = 0
    stats = {
        "files_scanned": 0,
        "by_ext": {},
        "interactive_elements": 0,
        "unreadable": 0,
    }

    lines_cache = {}

    def make_emit(rel, text):
        def emit(rule, severity, file, line, message, fix, snippet):
            if rules and rule not in rules:
                return
            lines = lines_cache.get(rel)
            if lines is None:
                lines = text.splitlines()
                lines_cache[rel] = lines
            ctx = ""
            for ln in (line - 1, line - 2):
                if 0 <= ln < len(lines):
                    ctx += lines[ln] + "\n"
            m = IGNORE_RE.search(ctx)
            if m and m.group(1).strip():
                nonlocal_counter["ignored"] += 1
                return
            if IGNORE_BARE_RE.search(ctx) and not m:
                nonlocal_counter["bare"] += 1
            findings.append({
                "rule": rule,
                "severity": severity,
                "file": file,
                "line": line,
                "message": message,
                "fix": fix,
                "fingerprint": hashlib.sha1(
                    ("%s|%s|%s" % (rule, file, snippet)).encode("utf-8")
                ).hexdigest()[:12],
            })
        return emit

    nonlocal_counter = {"ignored": 0, "bare": 0}

    for rel, full in files:
        text = read_text(full)
        if text is None:
            stats["unreadable"] += 1
            continue
        ext = os.path.splitext(rel)[1]
        stats["files_scanned"] += 1
        stats["by_ext"][ext] = stats["by_ext"].get(ext, 0) + 1
        emit = make_emit(rel, text)
        try:
            if ext in DART_EXT:
                sub = scan_dart(rel, text, emit)
            elif ext in WEB_EXT:
                sub = scan_markup(rel, text, php=False, emit=emit)
            else:
                sub = scan_markup(rel, text, php=True, emit=emit)
        except RecursionError:
            stats["unreadable"] += 1
            continue
        stats["interactive_elements"] += sub.get("interactive", 0)

    if project_checks:
        proj_emit = make_emit("<project>", "")
        scan_project(root, proj_emit, files)

    ignored = nonlocal_counter["ignored"]
    bare_pragma = nonlocal_counter["bare"]
    stats["ignored"] = ignored
    stats["bare_pragma"] = bare_pragma

    # Deterministic order + ordinal so repeated identical snippets stay distinct.
    findings.sort(key=lambda f: (f["file"], f["line"], f["rule"]))
    seen = {}
    for f in findings:
        key = (f["rule"], f["file"], f["fingerprint"])
        seen[key] = seen.get(key, 0) + 1
        f["key"] = "%s|%s|%s|%d" % (f["rule"], f["file"], f["fingerprint"], seen[key])

    return findings, stats


# ---------------------------------------------------------------------------
# Baseline (ratchet)
# ---------------------------------------------------------------------------

def load_baseline(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise ValueError("baseline must be an object with findings[]")
    return data


def baseline_error_count(data):
    counts = data.get("counts") or {}
    if isinstance(counts, dict) and isinstance(counts.get(ERROR), int):
        return counts[ERROR]
    return len(data.get("findings", []))


def build_baseline(findings, stats):
    errs = [f for f in findings if f["severity"] == ERROR]
    warns = [f for f in findings if f["severity"] == WARN]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "note": (
            "既知の a11y 違反スナップショット（ratchet 用）。新規違反のみ CI を落とす。"
            "件数は減る方向にのみ更新してよい — 増やす更新は --allow-baseline-growth が必要で、理由の記載が前提。"
        ),
        "counts": {ERROR: len(errs), WARN: len(warns)},
        "scanned": {"files": stats.get("files_scanned", 0), "interactive": stats.get("interactive_elements", 0)},
        "findings": sorted(f["key"] for f in findings),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def format_text_report(root, findings, stats, new_keys, baseline, strict):
    lines = []
    lines.append("root: %s" % root)
    ext_summary = ", ".join("%s=%d" % (k, v) for k, v in sorted(stats["by_ext"].items())) or "(none)"
    lines.append("scanned: %d files (%s) / interactive elements inspected: %d"
                 % (stats["files_scanned"], ext_summary, stats["interactive_elements"]))
    if stats.get("ignored"):
        lines.append("suppressed by `a11y-ignore: <reason>`: %d" % stats["ignored"])
    if stats.get("bare_pragma"):
        lines.append("NOTE: %d bare `a11y-ignore` without a reason — these do NOT suppress" % stats["bare_pragma"])
    if stats.get("unreadable"):
        lines.append("unreadable files: %d" % stats["unreadable"])

    if stats["files_scanned"] == 0:
        lines.append("")
        lines.append("UNKNOWN: 走査対象 0 件 — これはゼロ違反ではない（--root / --exclude を確認）")
        return "\n".join(lines)

    errs = [f for f in findings if f["severity"] == ERROR]
    warns = [f for f in findings if f["severity"] == WARN]
    lines.append("findings: %d error / %d warn" % (len(errs), len(warns)))

    if baseline is not None:
        known = set(baseline.get("findings", []))
        current = set(f["key"] for f in findings)
        lines.append("baseline: %d known / %d new / %d fixed (prune with --update-baseline)"
                     % (len(known), len(new_keys), len(known - current)))

    by_rule = {}
    by_file = {}
    for f in findings:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
        by_file[f["file"]] = by_file.get(f["file"], 0) + 1
    if by_rule:
        lines.append("by rule: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(by_rule.items())))
    if len(by_file) > 1:
        top = sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        lines.append("top files: " + ", ".join("%s=%d" % (k, v) for k, v in top))

    shown = [f for f in findings if (baseline is None or f["key"] in new_keys)]
    if shown:
        lines.append("")
        label = "NEW findings" if baseline is not None else "findings"
        lines.append("%s:" % label)
        for f in shown:
            lines.append("  %s:%d: %s [%s] %s" % (f["file"], f["line"], f["severity"].upper(), f["rule"], f["message"]))
            lines.append("      → fix: %s" % f["fix"])

    fatal = [f for f in shown if f["severity"] == ERROR or strict]
    lines.append("")
    if fatal:
        lines.append("FAIL: %d 件の%s違反（%s）" % (
            len(fatal),
            "新規" if baseline is not None else "",
            "error" + (" + warn (--strict)" if strict else ""),
        ))
        lines.append("静的に検出できない部分（読み上げでの通し操作・最大文字サイズのレイアウト）は"
                     "自動テストと実機確認で別途担保すること。")
    else:
        lines.append("OK: 新規の静的 a11y 違反なし（= アクセシブルであることの証明ではない）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Self-test (hermetic)
# ---------------------------------------------------------------------------

FIX_DART_VIOLATING = """
import 'package:flutter/material.dart';

class Bad extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // icon-only tap target with no name at all
        GestureDetector(
          onTap: () => go('/diary'),
          child: SizedBox(
            width: 44,
            height: 44,
            child: const Center(child: Icon(Icons.book)),
          ),
        ),
        // symbol-only label ('<' reads as "less than")
        GestureDetector(
          onTap: () => pop(),
          child: const Text('<'),
        ),
        // named by its text but no button role, and enabled state not exposed
        GestureDetector(
          onTap: enabled ? onTap : null,
          child: Text(label),
        ),
        // unnamed text field (hintText is empty)
        TextField(
          controller: _c,
          decoration: InputDecoration(hintText: ''),
        ),
        // unlabelled toggle
        Switch(value: v, onChanged: (x) {}),
        // informative image with no semanticLabel
        Image.asset('assets/hero.png'),
      ],
    );
  }
}
"""

FIX_DART_CLEAN = """
import 'package:flutter/material.dart';

class Good extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Semantics(
          button: true,
          label: '日記を開く',
          child: GestureDetector(
            onTap: () => go('/diary'),
            child: const Icon(Icons.book),
          ),
        ),
        Semantics(
          button: true,
          label: '戻る',
          child: GestureDetector(
            onTap: () => pop(),
            child: const ExcludeSemantics(child: Text('<')),
          ),
        ),
        Semantics(
          button: true,
          enabled: enabled,
          label: 'ログイン',
          child: GestureDetector(
            onTap: enabled ? onTap : null,
            child: Text(label),
          ),
        ),
        TextField(
          controller: _c,
          decoration: const InputDecoration(labelText: 'メッセージ'),
        ),
        SwitchListTile(
          title: const Text('通知'),
          value: v,
          onChanged: (x) {},
        ),
        Image.asset('assets/hero.png', semanticLabel: '海の写真'),
        IconButton(
          tooltip: '共有',
          onPressed: share,
          icon: const Icon(Icons.share),
        ),
      ],
    );
  }
}
"""

FIX_TSX_VIOLATING = """
export function Bad() {
  return (
    <div>
      <div onClick={() => save()}>保存</div>
      <button onClick={close}><Icon name="x" /></button>
      <img src="/hero.png" />
      <input type="text" />
      <a href="/next">×</a>
    </div>
  );
}
"""

FIX_TSX_CLEAN = """
export function Good() {
  return (
    <div>
      <button type="button" onClick={() => save()}>保存</button>
      <button type="button" aria-label="閉じる" onClick={close}>
        <Icon name="x" aria-hidden="true" />
      </button>
      <img src="/hero.png" alt="海の写真" />
      <img src="/deco.png" alt="" />
      <label htmlFor="q">検索</label>
      <input id="q" type="text" aria-label="検索語" />
      <a href="/next">次へ</a>
      <div role="button" tabIndex={0} onClick={save} onKeyDown={onKey}>代替</div>
    </div>
  );
}
"""

# Regression fixtures for false positives found while validating this gate against
# real i-Willink repos. Each one used to be reported and must stay silent.
FIX_TSX_LABEL_FORMS = """
export function Forms() {
  return (
    <form>
      {/* implicit association: the label wraps the input, no id needed */}
      <label>
        Campaign ID
        <input value={v} onChange={set} placeholder="issue-003" />
      </label>
      {/* explicit association: for/htmlFor points at the id */}
      <label htmlFor="subject">件名</label>
      <input id="subject" type="text" />
    </form>
  );
}
"""

FIX_TSX_CODE_SAMPLE = """
const snippet = `
<Button asChild>
  <Link href="/dashboard">ダッシュボードへ</Link>
</Button>
<img src="/x.png" />
<input type="text" />
`;

export function Docs() {
  return <pre>{snippet}</pre>;
}
"""

FIX_PHP_VIOLATING = """
<div class="card">
  <img src="<?php echo esc_url( $url ); ?>">
  <a href="#"></a>
  <input type="search" name="s">
</div>
"""

FIX_MARKUP_VIEWPORT = """
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
</head>
<body>
  <a href="/a" tabindex="3">先に読ませたいリンク</a>
</body>
"""

FIX_MARKUP_VIEWPORT_OK = """
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body>
  <a href="/a" tabindex="0">リンク</a>
  <div tabindex="-1">プログラム的にのみ focus</div>
</body>
"""

FIX_PHP_CLEAN = """
<div class="card">
  <img src="<?php echo esc_url( $url ); ?>" alt="<?php echo esc_attr( $alt ); ?>">
  <a href="<?php echo esc_url( $link ); ?>"><?php echo esc_html( $title ); ?></a>
  <label for="s">検索</label>
  <input type="search" id="s" name="s">
</div>
"""

FIX_DART_IGNORED = """
class Ignored extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    // a11y-ignore: 装飾用のヒット領域で、同じ操作が上のボタンで提供されている
    return GestureDetector(onTap: noop, child: const Icon(Icons.circle));
  }
}
"""

FIX_DART_BARE_IGNORE = """
class BareIgnore extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    // a11y-ignore
    return GestureDetector(onTap: noop, child: const Icon(Icons.circle));
  }
}
"""


def _write(base, rel, content):
    full = os.path.join(base, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)


def _rules(findings):
    out = {}
    for f in findings:
        out[f["rule"]] = out.get(f["rule"], 0) + 1
    return out


def run_self_test():
    import tempfile

    checks = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))

    with tempfile.TemporaryDirectory() as td:
        # --- Flutter: violating fixture -------------------------------------
        d1 = os.path.join(td, "flutter_bad")
        _write(d1, "lib/bad.dart", FIX_DART_VIOLATING)
        f1, s1 = scan_tree(d1, project_checks=False)
        r1 = _rules(f1)
        check("dart violating: NAME x1", r1.get("A11Y-FLUTTER-NAME") == 1, str(r1))
        check("dart violating: SYMBOL-LABEL x1", r1.get("A11Y-FLUTTER-SYMBOL-LABEL") == 1, str(r1))
        check("dart violating: ROLE x1", r1.get("A11Y-FLUTTER-ROLE") == 1, str(r1))
        check("dart violating: STATE warn x1", r1.get("A11Y-FLUTTER-STATE") == 1, str(r1))
        check("dart violating: FIELD-NAME x1", r1.get("A11Y-FLUTTER-FIELD-NAME") == 1, str(r1))
        check("dart violating: TOGGLE-NAME x1", r1.get("A11Y-FLUTTER-TOGGLE-NAME") == 1, str(r1))
        check("dart violating: IMAGE-LABEL warn x1", r1.get("A11Y-FLUTTER-IMAGE-LABEL") == 1, str(r1))
        check("dart violating: interactive counted", s1["interactive_elements"] >= 3, str(s1))

        # --- Flutter: clean fixture -----------------------------------------
        d2 = os.path.join(td, "flutter_good")
        _write(d2, "lib/good.dart", FIX_DART_CLEAN)
        f2, s2 = scan_tree(d2, project_checks=False)
        check("dart clean: 0 findings", len(f2) == 0, str(_rules(f2)))
        check("dart clean: files scanned = 1", s2["files_scanned"] == 1, str(s2))

        # --- Web (TSX) ------------------------------------------------------
        d3 = os.path.join(td, "web_bad")
        _write(d3, "src/Bad.tsx", FIX_TSX_VIOLATING)
        f3, _s3 = scan_tree(d3, project_checks=False)
        r3 = _rules(f3)
        check("tsx violating: INTERACTIVE-DIV x1", r3.get("A11Y-WEB-INTERACTIVE-DIV") == 1, str(r3))
        check("tsx violating: CONTROL-NAME x1", r3.get("A11Y-WEB-CONTROL-NAME") == 1, str(r3))
        check("tsx violating: IMG-ALT x1", r3.get("A11Y-WEB-IMG-ALT") == 1, str(r3))
        check("tsx violating: FIELD-NAME x1", r3.get("A11Y-WEB-FIELD-NAME") == 1, str(r3))
        check("tsx violating: SYMBOL-LABEL x1", r3.get("A11Y-WEB-SYMBOL-LABEL") == 1, str(r3))

        d4 = os.path.join(td, "web_good")
        _write(d4, "src/Good.tsx", FIX_TSX_CLEAN)
        f4, _s4 = scan_tree(d4, project_checks=False)
        errs4 = [f for f in f4 if f["severity"] == ERROR]
        check("tsx clean: 0 errors", len(errs4) == 0, str(_rules(f4)))

        # --- Markup (PHP) ---------------------------------------------------
        d5 = os.path.join(td, "php_bad")
        _write(d5, "theme/card.php", FIX_PHP_VIOLATING)
        f5, _s5 = scan_tree(d5, project_checks=False)
        r5 = _rules(f5)
        check("php violating: IMG-ALT x1", r5.get("A11Y-MARKUP-IMG-ALT") == 1, str(r5))
        check("php violating: CONTROL-NAME x1", r5.get("A11Y-MARKUP-CONTROL-NAME") == 1, str(r5))
        check("php violating: FIELD-NAME x1", r5.get("A11Y-MARKUP-FIELD-NAME") == 1, str(r5))

        d6 = os.path.join(td, "php_good")
        _write(d6, "theme/card.php", FIX_PHP_CLEAN)
        f6, _s6 = scan_tree(d6, project_checks=False)
        errs6 = [f for f in f6 if f["severity"] == ERROR]
        check("php clean: 0 errors (php echo counts as a name)", len(errs6) == 0, str(_rules(f6)))

        # --- clamp floor + unknown-ctor drift guard --------------------------
        d25 = os.path.join(td, "clamp")
        _write(d25, "lib/app.dart",
               "class App extends StatelessWidget {\n"
               "  @override\n"
               "  Widget build(BuildContext c) => MaterialApp(\n"
               "    builder: (c, child) => MediaQuery.withClampedTextScaling(maxScaleFactor: 1.6, child: child!),\n"
               "  );\n"
               "}\n")
        f25, _s25 = scan_tree(d25, project_checks=False)
        check("dart: clamping below 200% is an error",
              _rules(f25).get("A11Y-FLUTTER-SCALE-CLAMP") == 1, str(_rules(f25)))

        d26 = os.path.join(td, "clamp_ok")
        _write(d26, "lib/app.dart",
               "class App extends StatelessWidget {\n"
               "  @override\n"
               "  Widget build(BuildContext c) => MaterialApp(\n"
               "    builder: (c, child) => MediaQuery.withClampedTextScaling(maxScaleFactor: 2.0, child: child!),\n"
               "  );\n"
               "}\n")
        f26, _s26 = scan_tree(d26, project_checks=False)
        check("dart: clamping at 200% is clean",
              _rules(f26).get("A11Y-FLUTTER-SCALE-CLAMP") is None, str(_rules(f26)))

        d27 = os.path.join(td, "unknown_ctor")
        _write(d27, "lib/x.dart",
               "class X extends StatelessWidget {\n"
               "  @override\n"
               "  Widget build(BuildContext c) => ProjectSpecificTappable(onTap: go, child: const Icon(Icons.add));\n"
               "}\n")
        f27, _s27 = scan_tree(d27, project_checks=False)
        check("dart: an unclassified tappable ctor is reported, not silently skipped",
              _rules(f27).get("A11Y-FLUTTER-UNKNOWN-TAP-CTOR") == 1, str(_rules(f27)))

        d28 = os.path.join(td, "scale_test_deprecated")
        _write(d28, "pubspec.yaml", "name: demo\n")
        _write(d28, "lib/app.dart", "class A {}\n")
        _write(d28, "test/old_test.dart", "void main() { const x = 3.0; /* textScaleFactor */ }\n")
        f28, _s28 = scan_tree(d28, project_checks=True)
        check("project: a deprecated textScaleFactor test does not satisfy the large-text check",
              _rules(f28).get("A11Y-PROJ-SCALE-TEST") == 1, str(_rules(f28)))

        # --- viewport zoom lock + positive tabindex --------------------------
        d22 = os.path.join(td, "viewport")
        _write(d22, "theme/header.php", FIX_MARKUP_VIEWPORT)
        f22, _s22 = scan_tree(d22, project_checks=False)
        r22 = _rules(f22)
        check("markup: zoom-locked viewport is an error", r22.get("A11Y-MARKUP-VIEWPORT-ZOOM") == 1, str(r22))
        check("markup: positive tabindex is an error", r22.get("A11Y-MARKUP-POSITIVE-TABINDEX") == 1, str(r22))

        d23 = os.path.join(td, "viewport_ok")
        _write(d23, "theme/header.php", FIX_MARKUP_VIEWPORT_OK)
        f23, _s23 = scan_tree(d23, project_checks=False)
        check("markup: zoomable viewport + tabindex 0/-1 are clean", len(f23) == 0, str(_rules(f23)))

        d24 = os.path.join(td, "proj_next_subset")
        _write(d24, "package.json", '{"name":"demo","devDependencies":{"eslint-config-next":"^16"}}\n')
        _write(d24, "src/Page.tsx", "export const P = () => <button type=\"button\">保存</button>;\n")
        f24, _s24 = scan_tree(d24, project_checks=True)
        r24 = _rules(f24)
        check("project: Next.js preset alone is flagged as a subset, not as absent",
              r24.get("A11Y-PROJ-WEB-LINT-SUBSET") == 1 and r24.get("A11Y-PROJ-WEB-LINT") is None, str(r24))

        # --- false-positive regressions (found on real repos) ---------------
        d18 = os.path.join(td, "web_labels")
        _write(d18, "src/Forms.tsx", FIX_TSX_LABEL_FORMS)
        f18, s18 = scan_tree(d18, project_checks=False)
        check("tsx: implicit + explicit <label> association is a name",
              len(f18) == 0 and s18["files_scanned"] == 1, str(_rules(f18)))

        d19 = os.path.join(td, "web_code_sample")
        _write(d19, "src/Docs.tsx", FIX_TSX_CODE_SAMPLE)
        f19, s19 = scan_tree(d19, project_checks=False)
        check("tsx: JSX inside a template literal is not scanned",
              len(f19) == 0 and s19["files_scanned"] == 1, str(_rules(f19)))

        d21 = os.path.join(td, "proj_wp_theme")
        _write(d21, "package.json", '{"name":"theme","devDependencies":{"tailwindcss":"^3"}}\n')
        _write(d21, "header.php", "<header><a href=\"/\">Home</a></header>\n")
        f21, _s21 = scan_tree(d21, project_checks=True)
        check("project: PHP theme with a build package.json is not asked for a JSX linter",
              _rules(f21).get("A11Y-PROJ-WEB-LINT") is None, str(_rules(f21)))

        d20 = os.path.join(td, "proj_monorepo")
        _write(d20, "package.json", '{"name":"root","workspaces":["apps/*"]}\n')
        _write(d20, "apps/web/eslint.config.mjs", 'import next from "eslint-config-next/core-web-vitals";\n')
        _write(d20, "apps/web/src/Page.tsx", "export const P = () => <button type=\"button\">保存</button>;\n")
        f20, _s20 = scan_tree(d20, project_checks=True)
        check("project: monorepo workspace lint config is found",
              _rules(f20).get("A11Y-PROJ-WEB-LINT") is None, str(_rules(f20)))

        # --- suppression pragma --------------------------------------------
        d7 = os.path.join(td, "pragma")
        _write(d7, "lib/ignored.dart", FIX_DART_IGNORED)
        f7, s7 = scan_tree(d7, project_checks=False)
        check("pragma with reason suppresses", len(f7) == 0 and s7["ignored"] == 1, "%s %s" % (_rules(f7), s7))

        d8 = os.path.join(td, "pragma_bare")
        _write(d8, "lib/bare.dart", FIX_DART_BARE_IGNORE)
        f8, s8 = scan_tree(d8, project_checks=False)
        check("bare pragma does NOT suppress", len(f8) == 1 and s8["bare_pragma"] == 1, "%s %s" % (_rules(f8), s8))

        # --- exclusions (generated + test code) -----------------------------
        d9 = os.path.join(td, "excluded")
        _write(d9, "lib/model.g.dart", FIX_DART_VIOLATING)
        _write(d9, "test/widget_test.dart", FIX_DART_VIOLATING)
        f9, s9 = scan_tree(d9, project_checks=False)
        check("generated + test excluded -> 0 files scanned", s9["files_scanned"] == 0 and len(f9) == 0, str(s9))

        # --- ratchet: baseline suppresses known, catches new ----------------
        base = build_baseline(f1, s1)
        errs1 = [f for f in f1 if f["severity"] == ERROR]
        check("baseline counts errors", baseline_error_count(base) == len(errs1), str(base["counts"]))
        current_keys = set(f["key"] for f in f1)
        new_keys = current_keys - set(base["findings"])
        check("baseline: no new findings on identical tree", len(new_keys) == 0, str(new_keys))

        d10 = os.path.join(td, "flutter_bad_plus")
        _write(d10, "lib/bad.dart", FIX_DART_VIOLATING)
        _write(d10, "lib/extra.dart",
               "class E extends StatelessWidget {\n"
               "  @override\n"
               "  Widget build(BuildContext c) => GestureDetector(onTap: go, child: const Icon(Icons.add));\n"
               "}\n")
        f10, s10 = scan_tree(d10, project_checks=False)
        new10 = set(f["key"] for f in f10) - set(base["findings"])
        check("baseline: a new violation is NEW", len(new10) == 1, str(new10))

        # fingerprint stability: shifting lines must not create a NEW finding
        d11 = os.path.join(td, "flutter_bad_shifted")
        _write(d11, "lib/bad.dart", "// leading comment\n// another\n" + FIX_DART_VIOLATING)
        f11, _s11 = scan_tree(d11, project_checks=False)
        new11 = set(f["key"] for f in f11) - set(base["findings"])
        check("fingerprint stable under line shift", len(new11) == 0, str(new11))

        # anti-gaming: a baseline that grows is refused unless allowed
        grown = build_baseline(f10, s10)
        check("baseline growth detectable",
              baseline_error_count(grown) > baseline_error_count(base),
              "%s > %s" % (baseline_error_count(grown), baseline_error_count(base)))

        # --- empty tree = UNKNOWN, not zero --------------------------------
        d12 = os.path.join(td, "empty")
        os.makedirs(d12, exist_ok=True)
        f12, s12 = scan_tree(d12, project_checks=False)
        check("empty tree: 0 files scanned (=> UNKNOWN exit 3)", s12["files_scanned"] == 0 and len(f12) == 0, str(s12))

        # --- project-level checks ------------------------------------------
        d13 = os.path.join(td, "proj_flutter")
        _write(d13, "pubspec.yaml", "name: demo\n")
        _write(d13, "lib/app.dart", "class A {}\n")
        f13, _s13 = scan_tree(d13, project_checks=True)
        check("project: missing large-text test warns",
              _rules(f13).get("A11Y-PROJ-SCALE-TEST") == 1, str(_rules(f13)))

        d14 = os.path.join(td, "proj_flutter_ok")
        _write(d14, "pubspec.yaml", "name: demo\n")
        _write(d14, "lib/app.dart", "class A {}\n")
        _write(d14, "test/a11y_test.dart",
               "void main() { const TextScaler.linear(3.0); }\n")
        f14, _s14 = scan_tree(d14, project_checks=True)
        check("project: large-text test present -> no warn",
              _rules(f14).get("A11Y-PROJ-SCALE-TEST") is None, str(_rules(f14)))

        d15 = os.path.join(td, "proj_web")
        _write(d15, "package.json", '{"name":"demo","devDependencies":{}}\n')
        _write(d15, "src/Page.tsx", "export const P = () => <button type=\"button\">保存</button>;\n")
        f15, _s15 = scan_tree(d15, project_checks=True)
        check("project: missing a11y linter warns",
              _rules(f15).get("A11Y-PROJ-WEB-LINT") == 1, str(_rules(f15)))

        d16 = os.path.join(td, "proj_web_ok")
        _write(d16, "package.json", '{"name":"demo","devDependencies":{"eslint-plugin-jsx-a11y":"^6"}}\n')
        _write(d16, "src/Page.tsx", "export const P = () => <button type=\"button\">保存</button>;\n")
        f16, _s16 = scan_tree(d16, project_checks=True)
        check("project: a11y linter present -> no warn",
              _rules(f16).get("A11Y-PROJ-WEB-LINT") is None, str(_rules(f16)))

        # --- rule filter ----------------------------------------------------
        f17, _s17 = scan_tree(d1, rules={"A11Y-FLUTTER-NAME"}, project_checks=False)
        check("--rules filter keeps only the asked rule",
              set(_rules(f17).keys()) == {"A11Y-FLUTTER-NAME"}, str(_rules(f17)))

    all_ok = True
    for name, ok, detail in checks:
        print("[self-test] %-52s -> %s%s" % (name, "OK" if ok else "FAIL", "" if ok else "  (%s)" % detail))
        all_ok = all_ok and ok
    print("[self-test] %d checks" % len(checks))
    if all_ok:
        print("[self-test] RESULT: PASS")
        return 0
    print("[self-test] RESULT: FAIL")
    return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Accessibility static gate: detect interactive elements without an "
                    "accessible name / role / state across Flutter, React and PHP templates "
                    "(detection only — fixing is a human's job)."
    )
    ap.add_argument("--root", help="directory tree to scan")
    ap.add_argument("--baseline", help="known-violation snapshot (ratchet mode)")
    ap.add_argument("--update-baseline", action="store_true", help="rewrite the baseline from this scan")
    ap.add_argument("--allow-baseline-growth", action="store_true",
                    help="permit writing a baseline with MORE errors than before (must be justified)")
    ap.add_argument("--strict", action="store_true", help="warnings fail too")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument("--exclude", action="append", default=[], help="extra path substring to skip (repeatable)")
    ap.add_argument("--rules", help="comma-separated rule ids to run (default: all)")
    ap.add_argument("--no-project-checks", action="store_true", help="skip repo-level presence checks")
    ap.add_argument("--self-test", action="store_true", help="hermetic deterministic self-check")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(run_self_test())

    if not args.root:
        sys.stderr.write("usage: --root <dir> [--baseline <file>] (or --self-test)\n")
        sys.exit(2)
    if not os.path.isdir(args.root):
        sys.stderr.write("root not a directory: %s\n" % args.root)
        sys.exit(2)
    if args.update_baseline and not args.baseline:
        sys.stderr.write("--update-baseline requires --baseline <file>\n")
        sys.exit(2)

    rules = set(r.strip() for r in args.rules.split(",") if r.strip()) if args.rules else None

    findings, stats = scan_tree(
        args.root,
        excludes=args.exclude,
        rules=rules,
        project_checks=not args.no_project_checks,
    )

    baseline = None
    if args.baseline and os.path.exists(args.baseline):
        try:
            baseline = load_baseline(args.baseline)
        except (OSError, ValueError) as exc:
            sys.stderr.write("baseline read/parse error: %s\n" % exc)
            sys.exit(2)
    elif args.baseline and not args.update_baseline:
        # A missing baseline must never read as "nothing known, nothing new".
        sys.stderr.write(
            "baseline not found: %s — create it with --update-baseline (and commit it)\n" % args.baseline
        )
        sys.exit(2)

    new_keys = set()
    if baseline is not None:
        known = set(baseline.get("findings", []))
        new_keys = set(f["key"] for f in findings) - known
    else:
        new_keys = set(f["key"] for f in findings)

    if args.update_baseline:
        new_base = build_baseline(findings, stats)
        if baseline is not None and not args.allow_baseline_growth:
            before = baseline_error_count(baseline)
            after = baseline_error_count(new_base)
            if after > before:
                sys.stderr.write(
                    "refusing to grow the baseline: errors %d -> %d. Fix the new violations, or pass "
                    "--allow-baseline-growth with a written reason in the PR.\n" % (before, after)
                )
                sys.exit(1)
        with open(args.baseline, "w", encoding="utf-8") as fh:
            json.dump(new_base, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        print("baseline written: %s (error=%d warn=%d, from %d files)"
              % (args.baseline, new_base["counts"][ERROR], new_base["counts"][WARN], stats["files_scanned"]))
        if stats["files_scanned"] == 0:
            sys.stderr.write("UNKNOWN: 走査対象 0 件 — baseline を空で固定してはいけない（--root を確認）\n")
            sys.exit(3)
        sys.exit(0)

    if args.format == "json":
        print(json.dumps({
            "root": args.root,
            "stats": stats,
            "baseline": None if baseline is None else {"known": len(baseline.get("findings", []))},
            "findings": findings,
            "new": sorted(new_keys),
        }, ensure_ascii=False, indent=2))
    else:
        print(format_text_report(args.root, findings, stats, new_keys, baseline, args.strict))

    # "Zero files scanned" is UNKNOWN, never a pass (empty output != zero findings).
    if stats["files_scanned"] == 0:
        sys.exit(3)

    fatal = [f for f in findings if f["key"] in new_keys and (f["severity"] == ERROR or args.strict)]
    sys.exit(1 if fatal else 0)


if __name__ == "__main__":
    main()
