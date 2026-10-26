#!/usr/bin/env python3
"""Generate the Bash data table; this command never issues an approval."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib/shared/python"))
from software_catalog import load_snapshot, render_table


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    snapshot = load_snapshot(args.directory, require_approved=False, check_table=False)
    expected = render_table(snapshot)
    destination = args.directory / "software.tsv"
    if args.check:
        if not destination.is_file() or destination.read_text() != expected:
            parser.error("generated software.tsv is stale")
    else:
        destination.write_text(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
