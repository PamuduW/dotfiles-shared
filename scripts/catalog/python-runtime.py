#!/usr/bin/env python3
"""Acquire the catalog's private Python through its frozen owning product."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lib/shared/python"))
from catalog_context import load_qualification_context  # noqa: E402
from catalog_python_owner import selected_python_runtime  # noqa: E402
from software_catalog import CatalogError, freeze_approved_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--qualification-context", type=Path)
    args = parser.parse_args()
    try:
        if args.qualification_context:
            snapshot, sources, _ = load_qualification_context(args.qualification_context)
            if (
                sources["dotfiles-shared"] != ROOT
                or args.revision != snapshot.data["sources"]["dotfiles-shared"]
            ):
                raise CatalogError(
                    "qualification runtime launcher requires its frozen shared source"
                )
        else:
            snapshot = freeze_approved_snapshot(ROOT, args.catalog.parent, revision=args.revision)
        if snapshot.directory != args.catalog or snapshot.digest != args.digest:
            raise CatalogError("Python acquisition must use the selected operation snapshot")
        binary = selected_python_runtime(
            snapshot, ROOT, qualification_context=args.qualification_context
        )
        print(binary)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(main())
