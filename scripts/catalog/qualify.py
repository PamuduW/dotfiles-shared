#!/usr/bin/env python3
"""Aggregate real platform receipts against clean, frozen product sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/shared/python"))
from catalog_context import qualification_snapshot
from catalog_qualification import validate_results
from software_catalog import CatalogError, _object, _read


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--source", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--result", type=Path, action="append", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        sources = {}
        for item in args.source:
            name, separator, path = item.partition("=")
            if not separator or not path or name in sources:
                raise CatalogError("source arguments must have unique NAME=PATH identities")
            sources[name] = Path(path)
        snapshot = qualification_snapshot(args.catalog, args.digest, sources)
        results = [
            json.loads(_read(path, 4_194_304), object_pairs_hook=_object) for path in args.result
        ]
        if any(not isinstance(result, dict) for result in results):
            raise CatalogError("qualification receipts must be objects")
        result = validate_results(
            snapshot, results, expected_sources=snapshot.data["sources"], run_id=args.run_id
        )
        qualification_snapshot(args.catalog, args.digest, sources)
        print(json.dumps(result, indent=2, sort_keys=True))
    except (CatalogError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
