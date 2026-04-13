#!/usr/bin/env python3
import sys
from pathlib import Path

BLOCKED_PREFIXES = (
    "backend/scrapers/debug_html/",
    "root@",
)


def main(argv: list[str]) -> int:
    for raw in argv:
        p = Path(raw).as_posix()
        if any(p.startswith(prefix) for prefix in BLOCKED_PREFIXES):
            print(f"Blocked file path in commit: {p}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
