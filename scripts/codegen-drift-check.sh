#!/usr/bin/env bash
# =============================================================
# codegen-drift-check.sh — a generic codegen drift gate (config-driven, non-destructive).
#
# Purpose: deterministically verify that "running the generate command reproduces the
#          committed artifacts (the tracked files in the working tree) with zero diff".
#          Works for design-token codegen and any other codegen (tokens / TS types /
#          OpenAPI clients / protobuf, ...) via a config file.
#          It performs the classic CI pattern `<generate> && git diff --exit-code`, but
#          WITHOUT depending on git state — it targets only the declared artifacts and is
#          NON-DESTRUCTIVE (the working tree is left unchanged before/after generation).
#
#   Why this is needed:
#     If you hand-edit a generated artifact (e.g. a DS token CSS/TS file), or change the
#     source of truth but commit without regenerating, the invariant "artifact == source"
#     breaks (drift). Drift is the gap between "defined" and "the actual output matches",
#     which a self-report cannot detect. This gate regenerates, mechanically compares the
#     diff, and rejects drift with exit 1.
#
# NON-DESTRUCTIVE design (no destructive operations):
#   - Back up the declared artifacts (output files) to a temp dir, THEN run the generate
#     command, and compare the regenerated result against the temp backup.
#   - After comparing, ALWAYS restore from the backup. So the working tree is unchanged.
#     If generation created an output that did not exist before, remove ONLY that single
#     declared file (`rm -f <declared-file>` only — never `rm -rf` or directory removal).
#   - outputs are FILES ONLY (directories are unsupported = the blast radius is confined
#     to declared files).
#   - If the generate command mutates files outside the declared outputs, that side effect
#     is NOT restored (the generate command is assumed pure w.r.t. its declared outputs).
#   - git / hooks / settings are never touched. Wiring the gate into CI/hooks is a manual,
#     human step (see SKILL.md).
#
# Usage:
#   # config-driven (verify multiple targets at once)
#   codegen-drift-check.sh --config <path.json>
#
#   # ad-hoc (single target, no config)
#   codegen-drift-check.sh --cmd "<generate command>" --output <file> [--output <file> ...] \
#       [--workdir <dir>]
#
#   codegen-drift-check.sh --self-test        # deterministic gate (hermetic)
#
#   config JSON schema (parsed with python3, no jq dependency):
#     {
#       "workdir": "packages/tokens",          # optional (default "."). Shared cwd for all targets.
#       "targets": [
#         {
#           "name": "design tokens",           # display only (optional)
#           "cmd": "npm run tokens:build",      # generate command (required)
#           "workdir": "packages/tokens",       # optional (per-target override)
#           "outputs": [                        # generated artifact files (required, >=1)
#             "dist/tokens.css",
#             "dist/tokens.ts"
#           ]
#         }
#       ]
#     }
#   workdir precedence: target.workdir > config.workdir > --workdir > ".".
#   Relative output paths are resolved against each target's workdir.
#   An example config ships at examples/codegen-drift.config.example.
#
# Exit codes:
#   0 = all targets in-sync (artifact == regenerated result, no drift)
#   1 = one or more targets DRIFT (gate trips)
#   2 = bad argument / config schema (including output being a directory)
#   3 = cannot verify = UNKNOWN (generate command failed / config unparseable / missing tool)
#       NOTE: never read "unknown" as 0 findings (pass). An empty output is NOT a pass.
#
# Design: JSON parsing uses only the python3 standard library (no jq). BSD/macOS grep safe
#         (no grep -P). bash 3.2 compatible (no mapfile / associative arrays). The self-test
#         is hermetic (only a fake generator and fake artifacts inside a sandbox — no external
#         repo / network / git).
# =============================================================
set -uo pipefail

# ---- core logic: drift check for one target (non-destructive, restore guaranteed) ------
# check_target <workdir> <cmd> <outfile1> [outfile2 ...]
#   return: 0 = in-sync / 1 = DRIFT / 2 = output is a directory (invalid) / 3 = gen failed (unknown)
#   stdout: a verdict line per output (in-sync / DRIFT) and a short diff on DRIFT.
# Approach (non-destructive):
#   1) back up each output's content and existence flag to a temp dir
#   2) run the generate command in workdir
#   3) compare the regenerated result against the backup with cmp -s
#   4) restore from the backup (exists=cp restore / not-exists=rm -f the artifact)
#   NOTE: both real mode and the self-test call this SAME function (no hard-coded success).
check_target() {
  cdt_workdir="$1"; shift
  cdt_cmd="$1"; shift
  # remaining args = output files
  [ "$#" -ge 1 ] || { echo "  X 0 outputs (>=1 required)" >&2; return 2; }

  cdt_bkp="$(mktemp -d -t codegen_bkp.XXXXXX)" || { echo "  X mktemp failed" >&2; return 3; }

  # --- 1) back up (content + existence flag) ----
  cdt_i=0
  for cdt_f in "$@"; do
    case "$cdt_f" in
      /*) cdt_fp="$cdt_f" ;;
      *)  cdt_fp="$cdt_workdir/$cdt_f" ;;
    esac
    if [ -d "$cdt_fp" ]; then
      echo "  X output is a directory (files only): $cdt_f" >&2
      rm -rf "$cdt_bkp"
      return 2
    fi
    if [ -f "$cdt_fp" ]; then
      cp "$cdt_fp" "$cdt_bkp/$cdt_i.content" || { echo "  X backup failed: $cdt_f" >&2; rm -rf "$cdt_bkp"; return 3; }
      echo 1 > "$cdt_bkp/$cdt_i.existed"
    else
      echo 0 > "$cdt_bkp/$cdt_i.existed"
    fi
    cdt_i=$((cdt_i + 1))
  done

  # --- 2) run the generate command (in workdir; capture output, show only on failure) ----
  cdt_genlog="$(mktemp -t codegen_gen.XXXXXX)"
  ( cd "$cdt_workdir" && sh -c "$cdt_cmd" ) >"$cdt_genlog" 2>&1
  cdt_genrc=$?

  # --- 3) compare ----
  cdt_drift=0
  cdt_err=0
  if [ "$cdt_genrc" -ne 0 ]; then
    cdt_err=1
  else
    cdt_i=0
    for cdt_f in "$@"; do
      case "$cdt_f" in
        /*) cdt_fp="$cdt_f" ;;
        *)  cdt_fp="$cdt_workdir/$cdt_f" ;;
      esac
      cdt_existed="$(cat "$cdt_bkp/$cdt_i.existed")"
      if [ "$cdt_existed" = "1" ]; then
        if [ -f "$cdt_fp" ] && cmp -s "$cdt_bkp/$cdt_i.content" "$cdt_fp"; then
          echo "  OK in-sync: $cdt_f"
        else
          echo "  X DRIFT: ${cdt_f} (regenerated result differs from committed)"
          cdt_drift=1
          if [ -f "$cdt_fp" ]; then
            diff -u "$cdt_bkp/$cdt_i.content" "$cdt_fp" 2>/dev/null | sed -n '1,20p' | sed 's/^/      /'
          else
            echo "      (regeneration deleted the committed file)"
          fi
        fi
      else
        # generation created an output that was not committed -> drift (uncommitted artifact)
        if [ -f "$cdt_fp" ]; then
          echo "  X DRIFT: ${cdt_f} (generation created a file not committed)"
          cdt_drift=1
        else
          echo "  OK in-sync: ${cdt_f} (did not exist and was not generated)"
        fi
      fi
      cdt_i=$((cdt_i + 1))
    done
  fi

  # --- 4) restore (non-destructive guarantee) ----
  cdt_i=0
  for cdt_f in "$@"; do
    case "$cdt_f" in
      /*) cdt_fp="$cdt_f" ;;
      *)  cdt_fp="$cdt_workdir/$cdt_f" ;;
    esac
    cdt_existed="$(cat "$cdt_bkp/$cdt_i.existed")"
    if [ "$cdt_existed" = "1" ]; then
      cp "$cdt_bkp/$cdt_i.content" "$cdt_fp" 2>/dev/null || true
    else
      # remove only the declared file that did not exist before (no rm -rf / dir removal)
      [ -f "$cdt_fp" ] && rm -f "$cdt_fp"
    fi
    cdt_i=$((cdt_i + 1))
  done

  if [ "$cdt_err" -eq 1 ]; then
    echo "  X generate command failed (rc=${cdt_genrc}) -> cannot verify (unknown):" >&2
    sed -n '1,20p' "$cdt_genlog" | sed 's/^/      /' >&2
  fi
  rm -rf "$cdt_bkp" "$cdt_genlog"

  [ "$cdt_err" -eq 1 ] && return 3
  [ "$cdt_drift" -eq 1 ] && return 1
  return 0
}

# ---- config extraction (python3, no jq) ----
# emit_count <config>                → number of targets
# emit_field <config> <idx> <field>  → scalar field (cmd/name/workdir), no trailing newline
# emit_outputs <config> <idx>        → outputs, one path per line
# emit_config_workdir <config>       → top-level workdir (empty if absent)
cfg_py() {
  python3 - "$@" <<'PYEOF'
import sys, json
mode = sys.argv[1]
path = sys.argv[2]
try:
    with open(path) as f:
        cfg = json.load(f)
except Exception as e:
    sys.stderr.write("config unparseable: %s\n" % e)
    sys.exit(3)

if not isinstance(cfg, dict):
    sys.stderr.write("config top must be an object\n"); sys.exit(2)
targets = cfg.get("targets")
if not isinstance(targets, list) or not targets:
    sys.stderr.write("config.targets is empty/invalid (>=1 required)\n"); sys.exit(2)

if mode == "count":
    print(len(targets)); sys.exit(0)
if mode == "config_workdir":
    v = cfg.get("workdir")
    sys.stdout.write("" if v is None else str(v)); sys.exit(0)

idx = int(sys.argv[3])
t = targets[idx]
if not isinstance(t, dict):
    sys.stderr.write("target[%d] must be an object\n" % idx); sys.exit(2)

if mode == "outputs":
    outs = t.get("outputs")
    if not isinstance(outs, list) or not outs:
        sys.stderr.write("target[%d].outputs is empty/invalid (>=1 required)\n" % idx); sys.exit(2)
    for o in outs:
        print(str(o))
    sys.exit(0)

if mode == "field":
    field = sys.argv[4]
    v = t.get(field)
    if field == "cmd" and (v is None or str(v).strip() == ""):
        sys.stderr.write("target[%d].cmd is empty (required)\n" % idx); sys.exit(2)
    sys.stdout.write("" if v is None else str(v))
    sys.exit(0)

sys.stderr.write("unknown cfg_py mode: %s\n" % mode)
sys.exit(2)
PYEOF
}

# ---- print a CI/hook wiring hint (does not execute; applying is a manual step) ----
print_wiring_hint() {
  cat <<'EOF'

────────────────────────────────────────────────────────────
[DRIFT] The artifact diverged from its source (the "artifact == source" invariant broke).
[Fix (manual)] Do ONE of the following by hand, then re-run this gate and confirm in-sync:
  1) Run the generate command to refresh the artifact, then commit the diff
     (e.g. run `<generate command>`, `git add <outputs>` -> commit)
  2) If you had hand-edited the artifact, revert the edit, fix the source, and regenerate
[Wiring (manual)] To wire this gate into a CI required check / pre-commit check:
  - add `bash scripts/codegen-drift-check.sh --config <path>` to a CI job (read/generate/restore only)
  - making it a required status check on a protected branch is a self-lockout risk — get human
    approval first (it can block your own merges if the gate is misconfigured)
────────────────────────────────────────────────────────────
EOF
}

# ---- self-test (hermetic; actually calls check_target to verify) ----
self_test() {
  echo "-- codegen-drift-check self-test (hermetic; check_target logic) --"
  st_fail=0

  st_assert_rc() { # label expected got
    if [ "$2" = "$3" ]; then
      echo "  OK $1 (rc=$3)"
    else
      echo "  X $1 (expected rc=$2, got rc=$3)"
      st_fail=1
    fi
  }

  st_sandbox="$(mktemp -d -t codegen_selftest.XXXXXX)"

  # committed (tracked-file equivalent) artifact
  printf 'GENERATED-A\n' > "$st_sandbox/out.txt"

  # fake generator: writes env PAYLOAD to out.txt (deterministic)
  cat > "$st_sandbox/gen.sh" <<'GEOF'
#!/usr/bin/env bash
printf '%s\n' "${PAYLOAD:-GENERATED-A}" > out.txt
GEOF
  chmod +x "$st_sandbox/gen.sh"

  # fake generator: creates a new.txt that does not exist
  cat > "$st_sandbox/gen_new.sh" <<'GEOF'
#!/usr/bin/env bash
printf 'BRAND-NEW\n' > new.txt
GEOF
  chmod +x "$st_sandbox/gen_new.sh"

  # fake generator: fails (exit 7)
  cat > "$st_sandbox/gen_fail.sh" <<'GEOF'
#!/usr/bin/env bash
echo "boom" >&2
exit 7
GEOF
  chmod +x "$st_sandbox/gen_fail.sh"

  # fake generator: 2 outputs, changes only two.txt
  printf 'ONE-A\n' > "$st_sandbox/one.txt"
  printf 'TWO-A\n' > "$st_sandbox/two.txt"
  cat > "$st_sandbox/gen_multi.sh" <<'GEOF'
#!/usr/bin/env bash
printf 'ONE-A\n' > one.txt
printf 'TWO-B\n' > two.txt
GEOF
  chmod +x "$st_sandbox/gen_multi.sh"

  # --- Case 1: in-sync (generator reproduces the committed content) -> 0 ---
  PAYLOAD="GENERATED-A" check_target "$st_sandbox" "./gen.sh" "out.txt" >/dev/null 2>&1
  st_assert_rc "sync -> in-sync" 0 "$?"
  st_got="$(cat "$st_sandbox/out.txt")"
  if [ "$st_got" = "GENERATED-A" ]; then
    echo "  OK restore: committed content unchanged after sync"
  else
    echo "  X restore broken (sync): '$st_got'"; st_fail=1
  fi

  # --- Case 2: drift (generator produces different content) -> 1, and restored ---
  PAYLOAD="GENERATED-B" check_target "$st_sandbox" "./gen.sh" "out.txt" >/dev/null 2>&1
  st_assert_rc "drift -> DRIFT" 1 "$?"
  st_got="$(cat "$st_sandbox/out.txt")"
  if [ "$st_got" = "GENERATED-A" ]; then
    echo "  OK restore: working tree unchanged after drift detection (non-destructive)"
  else
    echo "  X restore broken (after drift got '$st_got', was GENERATED-A)"; st_fail=1
  fi

  # --- Case 3: uncommitted new artifact -> drift, and the created file is removed on restore ---
  check_target "$st_sandbox" "./gen_new.sh" "new.txt" >/dev/null 2>&1
  st_assert_rc "new artifact -> DRIFT" 1 "$?"
  if [ ! -f "$st_sandbox/new.txt" ]; then
    echo "  OK restore: a previously-absent artifact was removed (rm -f single file only)"
  else
    echo "  X restore broken: new.txt still present"; st_fail=1
  fi

  # --- Case 4: generate command failed -> 3 (unknown; not treated as 0 findings) ---
  check_target "$st_sandbox" "./gen_fail.sh" "out.txt" >/dev/null 2>&1
  st_assert_rc "generate failed -> unknown" 3 "$?"
  st_got="$(cat "$st_sandbox/out.txt")"
  if [ "$st_got" = "GENERATED-A" ]; then
    echo "  OK restore: committed content unchanged even on generate failure"
  else
    echo "  X restore broken (after generate failure got '$st_got')"; st_fail=1
  fi

  # --- Case 5: multiple outputs, only one drifts -> 1 ---
  check_target "$st_sandbox" "./gen_multi.sh" "one.txt" "two.txt" >/dev/null 2>&1
  st_assert_rc "multi output, 1 drifts -> DRIFT" 1 "$?"
  if [ "$(cat "$st_sandbox/one.txt")" = "ONE-A" ] && [ "$(cat "$st_sandbox/two.txt")" = "TWO-A" ]; then
    echo "  OK restore: multiple outputs all restored to committed content"
  else
    echo "  X restore broken (multi)"; st_fail=1
  fi

  # --- Case 6: output is a directory -> 2 (invalid) ---
  mkdir -p "$st_sandbox/adir"
  check_target "$st_sandbox" "true" "adir" >/dev/null 2>&1
  st_assert_rc "output is a directory -> invalid" 2 "$?"

  # --- Case 7: config extraction (cfg_py) against real data ---
  cat > "$st_sandbox/cfg.json" <<JEOF
{
  "workdir": ".",
  "targets": [
    { "name": "t1", "cmd": "./gen.sh", "outputs": ["out.txt"] },
    { "name": "t2", "cmd": "./gen_multi.sh", "workdir": "sub", "outputs": ["one.txt", "two.txt"] }
  ]
}
JEOF
  st_cnt="$(cfg_py count "$st_sandbox/cfg.json" 2>/dev/null)"
  st_assert_rc "cfg count=2" 2 "$st_cnt"
  st_cmd0="$(cfg_py field "$st_sandbox/cfg.json" 0 cmd 2>/dev/null)"
  if [ "$st_cmd0" = "./gen.sh" ]; then echo "  OK cfg target0 cmd extracted"; else echo "  X cfg cmd extract: '$st_cmd0'"; st_fail=1; fi
  st_wd1="$(cfg_py field "$st_sandbox/cfg.json" 1 workdir 2>/dev/null)"
  if [ "$st_wd1" = "sub" ]; then echo "  OK cfg target1 workdir override extracted"; else echo "  X cfg workdir extract: '$st_wd1'"; st_fail=1; fi
  st_outn="$(cfg_py outputs "$st_sandbox/cfg.json" 1 2>/dev/null | grep -c .)"
  if [ "$st_outn" = "2" ]; then echo "  OK cfg target1 outputs=2"; else echo "  X cfg outputs count: '$st_outn'"; st_fail=1; fi

  # --- Case 8: config unparseable -> cfg_py exits 3 (unknown) ---
  printf '{ not valid json' > "$st_sandbox/bad.json"
  cfg_py count "$st_sandbox/bad.json" >/dev/null 2>&1
  st_assert_rc "config unparseable -> unknown" 3 "$?"

  rm -rf "$st_sandbox"

  if [ "$st_fail" -eq 0 ]; then
    echo "OK SELF-TEST PASS (drift verdict + non-destructive restore + config extraction all match)"
    return 0
  fi
  echo "X SELF-TEST FAIL (logic broken)"
  return 1
}

# ---- argument parsing --------------------------------------------------
MODE="run"
CONFIG=""
ADHOC_CMD=""
CLI_WORKDIR=""
ADHOC_OUTPUTS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) MODE="self-test"; shift ;;
    --config)    CONFIG="${2:-}"; shift 2 ;;
    --cmd)       ADHOC_CMD="${2:-}"; shift 2 ;;
    --output)    ADHOC_OUTPUTS+=("${2:-}"); shift 2 ;;
    --workdir)   CLI_WORKDIR="${2:-}"; shift 2 ;;
    -h|--help)
      grep -E '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ "$MODE" = "self-test" ]; then
  self_test
  exit $?
fi

# ---- run mode ---------------------------------------------------------
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 3; }

DEFAULT_WORKDIR="${CLI_WORKDIR:-.}"

# mutually exclusive: --config vs (--cmd + --output)
if [ -n "$CONFIG" ]; then
  if [ -n "$ADHOC_CMD" ] || [ "${#ADHOC_OUTPUTS[@]}" -gt 0 ]; then
    echo "X --config and --cmd/--output are mutually exclusive (pick one)" >&2
    exit 2
  fi
  [ -f "$CONFIG" ] || { echo "X config does not exist: $CONFIG" >&2; exit 2; }
elif [ -n "$ADHOC_CMD" ]; then
  if [ "${#ADHOC_OUTPUTS[@]}" -eq 0 ]; then
    echo "X --cmd requires at least one --output" >&2
    exit 2
  fi
else
  echo "usage: codegen-drift-check.sh --config <path.json>" >&2
  echo "       codegen-drift-check.sh --cmd \"<gen>\" --output <file> [--output <file> ...] [--workdir <dir>]" >&2
  echo "       codegen-drift-check.sh --self-test" >&2
  exit 2
fi

overall_drift=0
overall_err=0
n_ok=0
n_drift=0
n_err=0

run_one() { # name workdir cmd out1 [out2 ...]
  ro_name="$1"; shift
  ro_workdir="$1"; shift
  ro_cmd="$1"; shift
  echo "-- target: ${ro_name} (workdir=${ro_workdir}) --"
  echo "   cmd: ${ro_cmd}"
  check_target "$ro_workdir" "$ro_cmd" "$@"
  ro_rc=$?
  case "$ro_rc" in
    0) echo "  -> IN-SYNC"; n_ok=$((n_ok + 1)) ;;
    1) echo "  -> DRIFT"; overall_drift=1; n_drift=$((n_drift + 1)); print_wiring_hint ;;
    3) echo "  -> cannot verify (unknown)"; overall_err=1; n_err=$((n_err + 1)) ;;
    2) echo "  -> bad config (output is not a file, etc.)"; overall_err=1; n_err=$((n_err + 1)) ;;
  esac
}

if [ -n "$CONFIG" ]; then
  N="$(cfg_py count "$CONFIG")" || { echo "X failed to read config -> unknown" >&2; exit 3; }
  CFG_WORKDIR="$(cfg_py config_workdir "$CONFIG" 2>/dev/null)"
  idx=0
  while [ "$idx" -lt "$N" ]; do
    t_name="$(cfg_py field "$CONFIG" "$idx" name 2>/dev/null)"
    [ -n "$t_name" ] || t_name="target[$idx]"
    t_cmd="$(cfg_py field "$CONFIG" "$idx" cmd)" || { echo "X target[$idx].cmd invalid" >&2; exit 2; }
    t_wd="$(cfg_py field "$CONFIG" "$idx" workdir 2>/dev/null)"
    # workdir precedence: target > config > CLI/default
    if [ -n "$t_wd" ]; then
      eff_wd="$t_wd"
    elif [ -n "$CFG_WORKDIR" ]; then
      eff_wd="$CFG_WORKDIR"
    else
      eff_wd="$DEFAULT_WORKDIR"
    fi
    [ -d "$eff_wd" ] || { echo "X workdir does not exist: ${eff_wd} (target ${idx})" >&2; exit 2; }

    # read outputs into an array (bash 3.2: build with while-read)
    t_outs=()
    while IFS= read -r line; do
      [ -n "$line" ] && t_outs+=("$line")
    done <<EOF
$(cfg_py outputs "$CONFIG" "$idx")
EOF
    if [ "${#t_outs[@]}" -eq 0 ]; then
      echo "X target[$idx].outputs extraction failed" >&2; exit 2
    fi
    run_one "$t_name" "$eff_wd" "$t_cmd" "${t_outs[@]}"
    idx=$((idx + 1))
  done
else
  [ -d "$DEFAULT_WORKDIR" ] || { echo "X workdir does not exist: $DEFAULT_WORKDIR" >&2; exit 2; }
  run_one "adhoc" "$DEFAULT_WORKDIR" "$ADHOC_CMD" "${ADHOC_OUTPUTS[@]}"
fi

echo ""
echo "-- summary: IN-SYNC=${n_ok} / DRIFT=${n_drift} / UNKNOWN=${n_err} --"

# Drift is a deterministic gate failure (exit 1). No drift but unknown-only -> exit 3 (not a pass).
if [ "$overall_drift" -eq 1 ]; then
  exit 1
fi
if [ "$overall_err" -eq 1 ]; then
  echo "  ! some targets could not be verified -> unknown (not a pass; empty output != pass)" >&2
  exit 3
fi
exit 0
