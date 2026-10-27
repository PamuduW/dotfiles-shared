#!/usr/bin/env python3
"""Audit complete fresh/repeat smoke coverage; never approve a catalog."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/shared/python"))
from catalog_context import load_qualification_context
from catalog_smoke import audit_smoke
from software_catalog import CatalogError, _object, _read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context", type=Path)
    parser.add_argument("--receipt", type=Path, required=True, action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.output.exists() or args.output.is_symlink():
            raise CatalogError("preserving existing smoke audit evidence")
        snapshot, _sources, context_digest = load_qualification_context(args.context)
        receipts = [
            json.loads(_read(path, 4_194_304), object_pairs_hook=_object) for path in args.receipt
        ]
        result = audit_smoke(snapshot, receipts)
        if load_qualification_context(args.context)[2] != context_digest:
            raise CatalogError("smoke audit context changed")
        with args.output.open("x") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    except (CatalogError, OSError, ValueError) as error:
        print(f"Smoke inventory audit held: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
