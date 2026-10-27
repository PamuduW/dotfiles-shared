#!/usr/bin/env python3
"""Read GitHub evidence and emit a held result or an unexecuted publication plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/shared/python"))
from catalog_github import GhApi, RunIdentity, decode_json
from catalog_publication import publication_status
from software_catalog import CatalogError, _read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "policy", type=Path, help="reviewed local controller policy; never a downloaded artifact"
    )
    args = parser.parse_args()
    try:
        policy = decode_json(_read(args.policy, 65_536))
        if (
            not isinstance(policy, dict)
            or set(policy)
            != {
                "schema_version",
                "identity",
                "expected_heads",
                "expected_sources",
                "required_jobs",
                "publisher_app_id",
            }
            or type(policy["schema_version"]) is not int
            or policy["schema_version"] != 1
        ):
            raise CatalogError("publication policy schema mismatch")
        identity = policy.pop("identity")
        if not isinstance(identity, dict) or set(identity) != {
            "repository_id",
            "workflow_id",
            "run_id",
            "attempt",
            "candidate",
            "branch",
            "workflow_revision",
        }:
            raise CatalogError("trusted workflow identity schema mismatch")
        policy.pop("schema_version")
        result = publication_status(GhApi(), RunIdentity(**identity), **policy)
    except (CatalogError, OSError) as error:
        result = {"schema_version": 1, "status": "held", "published": False, "reason": str(error)}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["status"] == "planned" else 2


if __name__ == "__main__":
    raise SystemExit(main())
