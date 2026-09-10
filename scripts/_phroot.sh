#!/bin/bash
# 検査対象リポジトリのルート解決（ハーネス共通・shell 版）。
# 詳細な理由は _phroot.py の docstring を参照。
# 解決順: PH_TARGET_ROOT -> git rev-parse --show-toplevel -> cwd

ph_target_root() {
  if [ -n "${PH_TARGET_ROOT:-}" ]; then
    printf '%s\n' "$PH_TARGET_ROOT"
    return 0
  fi
  local top
  if top="$(git rev-parse --show-toplevel 2>/dev/null)" && [ -n "$top" ]; then
    printf '%s\n' "$top"
    return 0
  fi
  pwd
}

# ハーネス自身の install 先（同梱 fixture / docs 用）
ph_harness_home() {
  (cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
}
