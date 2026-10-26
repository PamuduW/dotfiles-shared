#!/usr/bin/env python3
"""Resolve a complete proposal in isolation; never approve or publish it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/shared/python"))
from catalog_proposal import propose
from software_catalog import CatalogError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--source", required=True, action="append", metavar="NAME=PATH")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--seed", action="store_true", help="explicitly prepare the first unapproved catalog"
    )
    args = parser.parse_args()
    try:
        sources = {}
        for value in args.source:
            name, separator, path = value.partition("=")
            if not separator or not path or name in sources:
                raise CatalogError("source arguments require unique NAME=PATH identities")
            sources[name] = Path(path)
        snapshot, changed = propose(args.catalog, sources, args.output, seed=args.seed)
        print(
            json.dumps(
                {
                    "directory": str(snapshot.directory),
                    "catalog_digest": snapshot.digest,
                    "sources": dict(snapshot.data["sources"]),
                    "changed": changed,
                    "approved": False,
                },
                indent=2,
            )
        )
    except (CatalogError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
