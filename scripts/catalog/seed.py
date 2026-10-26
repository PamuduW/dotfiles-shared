#!/usr/bin/env python3
"""Prepare first-seed tooling pins without approving or installing software."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/shared/python"))
from catalog_context import _verify_source
from catalog_discovery import _mutable, fetch_metadata, write_candidate
from catalog_seed import TOOLS, build_bootstrap
from discover import _runtime_resolver
from software_catalog import CatalogError, _git, load_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agentbot-source", type=Path, required=True)
    parser.add_argument("--dotfiles-source", type=Path, required=True)
    args = parser.parse_args()
    try:
        baseline = load_snapshot(args.catalog, require_approved=False)
        if baseline.approved or baseline.data["auto_promote"]:
            raise CatalogError("bootstrap discovery requires an unapproved, disabled seed")
        if args.output.exists() or args.output.is_symlink():
            raise CatalogError("candidate output already exists; preserving it")
        roots = {
            "agentbot": args.agentbot_source.resolve(),
            "dotfiles": args.dotfiles_source.resolve(),
            "dotfiles-shared": Path(__file__).resolve().parents[2],
        }
        sources = {name: _git(root, "rev-parse", "HEAD") for name, root in roots.items()}
        for name, root in roots.items():
            _verify_source(name, root, sources[name])
        data = _mutable(baseline.data)
        data["sources"] = sources
        with tempfile.TemporaryDirectory(prefix="catalog-seed-") as temporary:
            basis = write_candidate(baseline, data, Path(temporary) / "basis", {})
            resolver = _runtime_resolver(roots["dotfiles"], basis, None, {})
            candidate = build_bootstrap(basis, fetch=fetch_metadata, runtime_resolver=resolver)
            for name, root in roots.items():
                _verify_source(name, root, sources[name])
            proposed = write_candidate(basis, candidate, args.output, {})
        print(
            json.dumps(
                {
                    "directory": str(proposed.directory),
                    "catalog_digest": proposed.digest,
                    "sources": sources,
                    "approved": False,
                    "bootstrap_tools": sorted(TOOLS),
                },
                indent=2,
            )
        )
    except (CatalogError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
