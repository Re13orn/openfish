#!/usr/bin/env python3
"""CLI entrypoint for event consistency checks."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.event_consistency import scan_event_consistency


def main() -> int:
    root = ROOT
    src_dir = root / "src"
    py_files = list(src_dir.rglob("*.py"))
    violations = scan_event_consistency(py_files)
    if not violations:
        print("event consistency check passed")
        return 0
    print("event consistency check failed:")
    for violation in violations:
        print(f"- {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
