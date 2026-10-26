#!/usr/bin/env python3
"""Resolve a whole-catalog proposal as JSON; never modify the approved catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/shared/python"))
from catalog_discovery import build_candidate, write_candidate
from catalog_proposal import runtime_resolver as _runtime_resolver
from software_catalog import CatalogError, load_snapshot, resolve_entry


def _check_arguments(parser, args):
    if args.bootstrap_seed and not all(
        (
            args.output,
            args.npm,
            args.node,
            args.uv,
            args.target,
            args.agentbot_source,
            args.dotfiles_source,
        )
    ):
        parser.error("initial seed discovery requires every tool, output and owner-source argument")
    selected = (args.npm, args.node, args.target)
    if (args.npm is not None or args.node is not None) and (
        not all(selected) or args.output is None
    ):
        parser.error("npm lock resolution requires --npm, --node, --target and --output together")
    if args.target is not None and args.npm is None and args.uv is None:
        parser.error("--target needs an explicit --npm/--node or --uv tool selection")
    if args.dotfiles_source is not None and args.output is None:
        parser.error("native runtime discovery requires a new --output directory")
    if args.agentbot_source is not None and args.output is None:
        parser.error("VSIX discovery requires a new --output directory")
    if args.uv is not None and (
        args.agentbot_source is None or args.target is None or args.output is None
    ):
        parser.error("Python locks require --uv, --target, --agentbot-source and --output")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument(
        "--output", type=Path, help="new directory for unapproved data and generated locks"
    )
    parser.add_argument(
        "--npm", type=Path, help="explicit approved npm executable for lock resolution"
    )
    parser.add_argument(
        "--node", type=Path, help="explicit approved Node executable for lock resolution"
    )
    parser.add_argument("--uv", type=Path, help="explicit approved uv executable for Python locks")
    parser.add_argument("--target", help="native target of the selected lock-resolution tools")
    parser.add_argument(
        "--agentbot-source", type=Path, help="clean frozen Agentbot source for VSIX discovery"
    )
    parser.add_argument(
        "--dotfiles-source",
        type=Path,
        help="clean frozen Dotfiles source for native runtime discovery",
    )
    parser.add_argument(
        "--bootstrap-seed",
        action="store_true",
        help="resolve the initial unapproved seed using explicitly installed bootstrap tool pins",
    )
    args = parser.parse_args()
    _check_arguments(parser, args)
    try:
        snapshot = load_snapshot(args.catalog, require_approved=False)
        if args.bootstrap_seed and (snapshot.approved or snapshot.data["auto_promote"]):
            raise CatalogError(
                "initial seed discovery requires unapproved data and disabled promotion"
            )
        require_approved = not args.bootstrap_seed
        if args.output is not None and (args.output.exists() or args.output.is_symlink()):
            raise CatalogError("candidate output already exists; preserving it")
        tools = None
        if args.npm is not None:
            tools = {
                "npm": args.npm,
                "node": args.node,
                "npm_version": resolve_entry(
                    snapshot, "npm", args.target, require_approved=require_approved
                )["version"],
                "node_version": resolve_entry(
                    snapshot, "nodejs", args.target, require_approved=require_approved
                )["version"],
            }
        locks = {}
        resolver = None
        python_resolver = None
        if args.agentbot_source is not None:
            from catalog_context import _verify_source

            _verify_source("agentbot", args.agentbot_source, snapshot.data["sources"]["agentbot"])
            owner = args.agentbot_source.resolve()
            sys.path.insert(0, str(owner))
            from src import catalog_extension_discovery

            if not Path(catalog_extension_discovery.__file__).resolve().is_relative_to(owner):
                raise CatalogError("VSIX resolver was imported from a different Agentbot source")
            resolver = catalog_extension_discovery.ExtensionCandidateResolver(locks).resolve
        if args.uv is not None:
            from src import catalog_python_discovery

            if not Path(catalog_python_discovery.__file__).resolve().is_relative_to(owner):
                raise CatalogError("Python resolver was imported from a different Agentbot source")
            uv_version = resolve_entry(
                snapshot, "uv", args.target, require_approved=require_approved
            )["version"]
            python_resolver = catalog_python_discovery.PythonCandidateResolver(
                owner, locks, uv=args.uv, uv_version=uv_version
            ).resolve
        runtime_resolver = _runtime_resolver(
            args.dotfiles_source, snapshot, args.uv, locks, require_approved=require_approved
        )
        candidate = build_candidate(
            snapshot,
            npm_tools=tools,
            locks=locks,
            extension_resolver=resolver,
            python_resolver=python_resolver,
            runtime_resolver=runtime_resolver,
        )
        if args.bootstrap_seed:
            from catalog_seed import verify_bootstrap_tools

            verify_bootstrap_tools(snapshot.data, candidate)
        if args.output is None:
            print(json.dumps(candidate, indent=2, sort_keys=True))
        else:
            proposed = write_candidate(snapshot, candidate, args.output, locks)
            print(
                json.dumps(
                    {
                        "directory": str(proposed.directory),
                        "catalog_digest": proposed.digest,
                        "approved": False,
                        "changed": proposed.digest != snapshot.digest,
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
