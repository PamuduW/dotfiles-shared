#!/usr/bin/env python3
"""Plan or explicitly submit tested catalog data without bypassing protected review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/shared/python"))
from catalog_github import GhApi
from catalog_main_publication import plan_main_publication, submit_main_publication
from catalog_publication import load_policy, plan_publication
from catalog_writer import submit_publication
from catalog_writer_transport import GhAppWriter
from software_catalog import CatalogError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", type=Path, help="reviewed controller policy from trusted sources")
    parser.add_argument(
        "--execute", action="store_true", help="submit a development PR using the trusted App token"
    )
    parser.add_argument(
        "--development-pr",
        type=int,
        help="propose the identical catalog to main after this reviewed development merge",
    )
    args = parser.parse_args()
    try:
        identity, policy = load_policy(args.policy)
        if args.development_pr is not None:
            operation = submit_main_publication if args.execute else plan_main_publication
            api = GhAppWriter() if args.execute else GhApi()
            result = operation(
                api, identity, development_pull_request=args.development_pr, **policy
            )
        elif args.execute:
            result = submit_publication(GhAppWriter(), identity, **policy)
        else:
            result = plan_publication(GhApi(), identity, **policy)
    except (CatalogError, OSError) as error:
        result = {"schema_version": 1, "status": "held", "published": False, "reason": str(error)}
    print(json.dumps(result, sort_keys=True, indent=2))
    return (
        0 if result["status"] in {"planned", "submitted", "planned-main", "submitted-main"} else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
