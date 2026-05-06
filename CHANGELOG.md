# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [SemVer](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-05-06

### Added

- Codex plugin entrypoint (`.codex-plugin/plugin.json`) that mirrors Claude plugin core metadata.
- `skills/codex-build/` adapter skill for running the 5 phase build flow in Codex.
- Codex adoption guide and Claude-to-Codex sync check with CI workflow.

## [0.1.0] - 2026-04-24

### Added

- Initial scaffold: 4 standard agents (`dev-explorer`, `dev-planner`, `dev-tester`, `dev-reviewer`)
- `skills/dev-standards/` — stack-agnostic baseline
- `commands/build.md` — 5 phase build flow
- `examples/project-standards-template/` — domain-knowledge extension scaffold for downstream repos
- Plugin metadata (`.claude-plugin/plugin.json`, `marketplace.json`)
- MIT license

### Notes

- Status: **unstable**. Verification ongoing on internal products.
- Designed for Opus 4.7 (`model: inherit` throughout).
