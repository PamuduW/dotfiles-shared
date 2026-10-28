#!/usr/bin/env python3
"""Read GitHub evidence and emit a held result or an unexecuted publication plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/shared/python"))
from catalog_github import GhApi
from catalog_publication import load_policy, publication_status
from software_catalog import CatalogError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "policy", type=Path, help="reviewed local controller policy; never a downloaded artifact"
    )
    args = parser.parse_args()
    try:
        identity, policy = load_policy(args.policy)
        result = publication_status(GhApi(), identity, **policy)
    except (CatalogError, OSError) as error:
        result = {"schema_version": 1, "status": "held", "published": False, "reason": str(error)}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["status"] == "planned" else 2


if __name__ == "__main__":
    raise SystemExit(main())
