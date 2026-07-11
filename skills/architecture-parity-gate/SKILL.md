---
name: architecture-parity-gate
description: Detect where a code tree violates its declared architecture — config-declared allowed dependency directions plus per-layer naming rules. A language-agnostic, python-stdlib-only, detection-only gate (fixing is a human's job; wiring it into CI is a deliberate human step). Triggers: architecture violation, dependency direction check, layer boundary, naming-rule gate, architecture parity, dependency-direction gate.
allowed-tools: Bash, Read
---

# architecture-parity-gate (detection only)

> Given a config (JSON) that declares the *design intent* — (1) the allowed
> dependency directions between layers and (2) the per-layer file naming rules —
> this gate **mechanically detects where the code tree (`--root`) violates it**.
> "parity" = compare the declared design against the implementation tree.
> Language-agnostic and config-driven (import detection is delegated to the
> config's `import_markers` regexes).
> **This skill never rewrites code** (no fix / delete / rename — detection only).

## Scope and safety

- **Detection is read-only** — it walks local files only; no `gh` / `aws`, no
  network, no writes.
- **Fixing a violation is a human's job** — correcting a dependency direction or
  renaming a file happens in a PR. The gate reports; it does not repair.
- **Wiring this gate into CI is a deliberate, higher-risk step** — installing it
  as a required status check / branch protection changes what can block merges,
  so do it by hand, on purpose. Running it locally or in a PR check is low-risk.

## How to run

1. Declare the architectural intent as JSON (see the example config below). Keep a
   config per target repo.
2. Detect (read-only):
   ```
   python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/arch-parity-check.py" --config <config.json> --root <dir>
   ```
   - exit 0 = no violations (parity OK).
   - exit 1 = dependency and/or naming violations. Details (`file:line` + type) are printed.
   - exit 2 = usage / config read / parse error (do not read empty output as "0 violations").
3. On exit 1, a human fixes the printed violations **in a PR** (the gate does not fix them).
4. To gate CI, wire it into a workflow / branch protection **by hand**.

## Config schema (every field optional — a minimal config still runs)

A ready-to-copy example lives at
[`examples/arch-parity.config.example.json`](../../examples/arch-parity.config.example.json)
(a classic layered / clean architecture: `domain <- application <- infrastructure`).

```json
{
  "layers": {
    "domain":         {"roots": ["src/domain"],         "import_markers": ["from +domain", "import +domain"]},
    "application":    {"roots": ["src/application"],     "import_markers": ["from +application"]},
    "infrastructure": {"roots": ["src/infrastructure"], "import_markers": ["from +infrastructure"]}
  },
  "allowed_dependencies": {
    "domain": [],
    "application": ["domain"],
    "infrastructure": ["application", "domain"]
  },
  "naming": [
    {"applies_to": "domain", "must_match": "_entity\\.py$", "message": "domain files must be *_entity.py"},
    {"applies_to": "domain", "must_not_match": "Controller\\.py$", "message": "no Controller in domain"}
  ],
  "exclude": ["node_modules", "/test"]
}
```

- `layers[L].roots` — path prefixes of files that belong to layer L (classified by longest match).
- `layers[L].import_markers` — regexes that mean "some file imports layer L" (language-agnostic).
- `allowed_dependencies[L]` — whitelist of who L may depend on (same layer always allowed). `[]` = may depend on nothing.
- `naming[]` — `applies_to` (a layer or `*`), `path_regex` (narrow targets by relpath), `must_match` / `must_not_match` (basename regexes).
- `exclude` — skip files whose relpath contains any of these substrings (`.git/` is always excluded).

## Deterministic --check (self-test)

```
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/arch-parity-check.py" --self-test
```
- **exit 0** = the comparison logic (dependency whitelist + naming
  must_match/must_not_match + exclude + graceful bad-regex handling) matches on
  all built-in fixtures. The self-test writes a **compliant tree (-> 0 violations)**
  and a **violating tree (-> 2 dependency + 2 naming)** to a tempdir and compares
  (hermetic: no external dependency, no external writes).
- **exit 1** = a mismatch in any case (logic is broken).

## Related

- backing script: `scripts/arch-parity-check.py`
- stop primitive: `scripts/goal-loop.sh` (exit 0 = GOAL MET / 1 = CONTINUE / 2 = CAP REACHED)
