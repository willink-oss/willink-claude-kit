#!/usr/bin/env python3
"""Validate that Codex and Antigravity adapters are synchronized with Claude canonical files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "codex" / "sync-manifest.json"
CLAUDE_PLUGIN_PATH = ROOT / ".claude-plugin" / "plugin.json"
CLAUDE_MARKETPLACE_PATH = ROOT / ".claude-plugin" / "marketplace.json"
CODEX_PLUGIN_PATH = ROOT / ".codex-plugin" / "plugin.json"
CODEX_BUILD_SKILL_PATH = ROOT / "skills" / "codex-build" / "SKILL.md"
ANTIGRAVITY_BUILD_SKILL_PATH = ROOT / "skills" / "antigravity-build" / "SKILL.md"
README_PATH = ROOT / "README.md"
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
ADOPTION_DOC_PATHS = [
    ROOT / "docs" / "adoption-guide.md",
    ROOT / "docs" / "codex-adoption-guide.md",
    ROOT / "docs" / "antigravity-adoption-guide.md",
]

# A version pin *example* always carries the plugin-name key, e.g.
#   "willink-claude-kit@iwillink": ["2.0.0"]
# This deliberately does NOT match bare array/string forms used to illustrate the
# array-vs-string schema rule (e.g. `string "0.1.1"` / `array 形式 ["0.1.1"]`),
# so those documentation counter-examples are not treated as drift.
PLUGIN_PIN_RE = re.compile(r'"willink-claude-kit@iwillink":\s*\[\s*"(\d+\.\d+\.\d+)"\s*\]')
# Released CHANGELOG headers look like `## [2.0.0] - 2026-06-11`; `## [Unreleased]`
# carries no version and is intentionally skipped.
CHANGELOG_RELEASE_RE = re.compile(r"^##\s*\[(\d+\.\d+\.\d+)\]", re.MULTILINE)

REQUIRED_CANONICAL_PATHS = [
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "commands/build.md",
    "agents/dev-explorer.md",
    "agents/dev-planner.md",
    "agents/dev-tester.md",
    "agents/dev-reviewer.md",
    "skills/dev-standards/SKILL.md",
]

PLUGIN_MIRROR_FIELDS = [
    "name",
    "version",
    "author",
    "homepage",
    "license",
    "keywords",
]

REQUIRED_PHASES = [
    "Phase 1",
    "Phase 2",
    "Phase 3",
    "Phase 4",
    "Phase 5",
]

REQUIRED_ROLE_CONTRACTS = [
    "dev-explorer",
    "dev-planner",
    "dev-tester",
    "dev-reviewer",
]

REQUIRED_SHARED_PROJECT_PATHS = [
    ".claude/skills/project-standards/SKILL.md",
    ".claude/agent-memory/dev-reviewer/MEMORY.md",
]


def repo_path(path: str) -> Path:
    return ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Missing {MANIFEST_PATH.relative_to(ROOT)}")
    return read_json(MANIFEST_PATH)


def canonical_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    entries = manifest.get("canonicalFiles")
    if not isinstance(entries, list):
        raise ValueError("codex/sync-manifest.json must define canonicalFiles[]")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each canonicalFiles[] entry must be an object")
        if not isinstance(entry.get("path"), str):
            raise ValueError("Each canonicalFiles[] entry must include a string path")
        if "sha256" in entry and not isinstance(entry.get("sha256"), str):
            raise ValueError("Each canonicalFiles[] sha256 must be a string when present")
    return entries


def default_manifest() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "updatedAt": date.today().isoformat(),
        "sourceOfTruth": "Claude Code plugin files are canonical; Codex and Antigravity files are platform adapters checked for drift.",
        "canonicalFiles": [
            {
                "path": path,
                "sha256": sha256_file(repo_path(path)),
                "codexAdapters": [
                    ".codex-plugin/plugin.json",
                    "skills/codex-build/SKILL.md",
                ],
                "antigravityAdapters": [
                    "skills/antigravity-build/SKILL.md",
                ]
            }
            for path in REQUIRED_CANONICAL_PATHS
        ],
    }


def update_manifest() -> list[str]:
    manifest = load_manifest() if MANIFEST_PATH.exists() else default_manifest()
    existing_by_path = {
        entry["path"]: entry
        for entry in canonical_entries(manifest)
        if isinstance(entry.get("path"), str)
    }

    new_entries: list[dict[str, Any]] = []
    for path in REQUIRED_CANONICAL_PATHS:
        entry = dict(existing_by_path.get(path, {}))
        entry["path"] = path
        entry["sha256"] = sha256_file(repo_path(path))
        
        # Ensure default adapters are set if not present
        if "codexAdapters" not in entry:
            entry["codexAdapters"] = [".codex-plugin/plugin.json", "skills/codex-build/SKILL.md"]
        if "antigravityAdapters" not in entry:
            entry["antigravityAdapters"] = ["skills/antigravity-build/SKILL.md"]
            
        new_entries.append(entry)

    manifest["schemaVersion"] = 1
    manifest["updatedAt"] = date.today().isoformat()
    manifest["sourceOfTruth"] = (
        "Claude Code plugin files are canonical; Codex and Antigravity files are platform adapters checked for drift."
    )
    manifest["canonicalFiles"] = new_entries
    write_json(MANIFEST_PATH, manifest)
    return [f"updated {MANIFEST_PATH.relative_to(ROOT)}"]


def validate_canonical_hashes(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    entries = canonical_entries(manifest)
    paths_in_manifest = {entry["path"] for entry in entries}

    for path in REQUIRED_CANONICAL_PATHS:
        if path not in paths_in_manifest:
            errors.append(f"Missing canonical file in manifest: {path}")

    for entry in entries:
        path = entry["path"]
        full_path = repo_path(path)
        expected = entry.get("sha256")
        if not full_path.exists():
            errors.append(f"Manifest references missing file: {path}")
            continue
        actual = sha256_file(full_path)
        if expected != actual:
            errors.append(
                f"Drift detected for {path}: manifest sha256 {expected!r}, actual {actual!r}. "
                "Update Codex and Antigravity adapters, then run scripts/check_sync.py --update."
            )

    return errors


def validate_plugin_parity() -> list[str]:
    errors: list[str] = []
    claude_plugin = read_json(CLAUDE_PLUGIN_PATH)
    codex_plugin = read_json(CODEX_PLUGIN_PATH)

    for field in PLUGIN_MIRROR_FIELDS:
        if claude_plugin.get(field) != codex_plugin.get(field):
            errors.append(
                f".codex-plugin/plugin.json field {field!r} must mirror .claude-plugin/plugin.json"
            )

    if codex_plugin.get("skills") != "./skills/":
        errors.append('.codex-plugin/plugin.json must set "skills" to "./skills/"')

    interface = codex_plugin.get("interface")
    if not isinstance(interface, dict):
        errors.append(".codex-plugin/plugin.json must include interface metadata")
    else:
        long_description = str(interface.get("longDescription", ""))
        if "Codex" not in long_description or "Claude Code" not in long_description:
            errors.append("Codex plugin interface longDescription must mention both Codex and Claude Code")

    return errors


def validate_marketplace_release() -> list[str]:
    errors: list[str] = []
    claude_plugin = read_json(CLAUDE_PLUGIN_PATH)
    marketplace = read_json(CLAUDE_MARKETPLACE_PATH)
    plugin_name = claude_plugin.get("name")
    plugin_version = claude_plugin.get("version")
    expected_ref = f"{plugin_name}--v{plugin_version}"

    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        return [".claude-plugin/marketplace.json must define plugins[]"]

    entries = [entry for entry in plugins if isinstance(entry, dict) and entry.get("name") == plugin_name]
    if len(entries) != 1:
        return [f".claude-plugin/marketplace.json must contain exactly one entry for {plugin_name!r}"]

    entry = entries[0]
    if entry.get("version") != plugin_version:
        errors.append("Marketplace plugin version must match .claude-plugin/plugin.json version")

    source = entry.get("source")
    if not isinstance(source, dict):
        errors.append("Marketplace plugin entry must include a source object")
    elif source.get("ref") != expected_ref:
        errors.append(f"Marketplace source.ref must be {expected_ref!r}")

    return errors


def validate_codex_build_skill() -> list[str]:
    errors: list[str] = []
    if not CODEX_BUILD_SKILL_PATH.exists():
        return [f"Missing {CODEX_BUILD_SKILL_PATH.relative_to(ROOT)}"]

    text = CODEX_BUILD_SKILL_PATH.read_text(encoding="utf-8")
    for phase in REQUIRED_PHASES:
        if phase not in text:
            errors.append(f"codex-build skill must reference {phase}")
    for role in REQUIRED_ROLE_CONTRACTS:
        if role not in text:
            errors.append(f"codex-build skill must reference {role}")
    for shared_path in REQUIRED_SHARED_PROJECT_PATHS:
        if shared_path not in text:
            errors.append(f"codex-build skill must reference shared project path {shared_path}")
    if "explicit" not in text.lower() or "subagent" not in text.lower():
        errors.append("codex-build skill must state that Codex subagents require explicit user authorization")

    return errors


def validate_antigravity_build_skill() -> list[str]:
    errors: list[str] = []
    if not ANTIGRAVITY_BUILD_SKILL_PATH.exists():
        return [f"Missing {ANTIGRAVITY_BUILD_SKILL_PATH.relative_to(ROOT)}"]

    text = ANTIGRAVITY_BUILD_SKILL_PATH.read_text(encoding="utf-8")
    for phase in REQUIRED_PHASES:
        if phase not in text:
            errors.append(f"antigravity-build skill must reference {phase}")
    for role in REQUIRED_ROLE_CONTRACTS:
        if role not in text:
            errors.append(f"antigravity-build skill must reference {role}")
    for shared_path in REQUIRED_SHARED_PROJECT_PATHS:
        if shared_path not in text:
            errors.append(f"antigravity-build skill must reference shared project path {shared_path}")
    
    # Antigravity-specific validations
    if "planning mode" not in text.lower():
        errors.append("antigravity-build skill must reference Planning Mode")
    if "define_subagent" not in text.lower():
        errors.append("antigravity-build skill must reference define_subagent")

    return errors


def validate_release_consistency() -> list[str]:
    """Catch release-version drift that hash/parity checks miss.

    Guards the documented release surface against the plugin version: the latest
    CHANGELOG release header and every version-pin example in README + adoption
    docs must match `.claude-plugin/plugin.json`. Historical CHANGELOG entries and
    array-vs-string schema illustrations are intentionally allowed.
    """
    errors: list[str] = []
    claude_plugin = read_json(CLAUDE_PLUGIN_PATH)
    version = claude_plugin.get("version")
    if not isinstance(version, str):
        return [".claude-plugin/plugin.json must define a string version"]

    if not CHANGELOG_PATH.exists():
        errors.append("Missing CHANGELOG.md")
    else:
        releases = CHANGELOG_RELEASE_RE.findall(CHANGELOG_PATH.read_text(encoding="utf-8"))
        if not releases:
            errors.append("CHANGELOG.md must contain at least one '## [x.y.z]' release header")
        elif releases[0] != version:
            errors.append(
                f"CHANGELOG.md latest release '[{releases[0]}]' must match plugin version "
                f"{version!r}. Older release headers below are allowed."
            )

    for doc in [README_PATH, *ADOPTION_DOC_PATHS]:
        if not doc.exists():
            continue
        for pinned in PLUGIN_PIN_RE.findall(doc.read_text(encoding="utf-8")):
            if pinned != version:
                errors.append(
                    f"{doc.relative_to(ROOT)}: version pin example '[\"{pinned}\"]' must match "
                    f"plugin version {version!r}."
                )

    return errors


def run_check() -> int:
    errors: list[str] = []
    try:
        manifest = load_manifest()
        errors.extend(validate_canonical_hashes(manifest))
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))

    try:
        errors.extend(validate_plugin_parity())
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))

    try:
        errors.extend(validate_marketplace_release())
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))

    try:
        errors.extend(validate_codex_build_skill())
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))

    try:
        errors.extend(validate_antigravity_build_skill())
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))

    try:
        errors.extend(validate_release_consistency())
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))

    if errors:
        print("Adapter sync check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Adapter sync check passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate sync state")
    mode.add_argument("--update", action="store_true", help="refresh canonical file hashes")
    args = parser.parse_args()

    if args.update:
        for message in update_manifest():
            print(message)
        return 0

    return run_check()


if __name__ == "__main__":
    raise SystemExit(main())
