#!/usr/bin/env python3
"""Wrapper around scripts/check_sync.py for backward compatibility."""

import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK_SYNC = ROOT / "scripts" / "check_sync.py"

def main() -> int:
    result = subprocess.run([sys.executable, str(CHECK_SYNC)] + sys.argv[1:])
    return result.returncode

if __name__ == "__main__":
    raise SystemExit(main())
