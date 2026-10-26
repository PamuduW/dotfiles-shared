#!/usr/bin/env python3
"""Report strict rollout readiness without installing or approving software."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/shared/python"))
from catalog_qualification import observation_targets, qualification_components
from software_catalog import _UNVERSIONED, CatalogError, load_snapshot


def readiness(directory: Path) -> dict:
    snapshot = load_snapshot(directory, require_approved=False)
    missing: list[str] = []
    required = {key: set() for key in snapshot.data["components"]}
    for target in snapshot.data["required_targets"]:
        for component, artifact_target in observation_targets(snapshot, target).values():
            required[component].add(artifact_target)
    selected = qualification_components(snapshot)
    excluded = []
    for identity, component in snapshot.data["components"].items():
        if component["kind"] in _UNVERSIONED:
            continue
        if identity not in selected:
            excluded.append(identity)
            continue
        missing.extend(f"{identity}: {reason}" for reason in component["blockers"])
        if not component["discovery"]:
            missing.append(f"{identity}: publisher discovery is not configured")
        if not required[identity]:
            missing.append(
                f"{identity}: required platform matrix does not cover its declared hosts"
            )
        for target in sorted(required[identity]):
            if target not in component["targets"] and "any" not in component["targets"]:
                missing.append(f"{identity}: no exact artifact/lock for {target}")
    if not snapshot.approved:
        missing.append("initial real installation qualification has no approval")
    if not snapshot.data["auto_promote"]:
        missing.append("automatic promotion is disabled")
    return {
        "catalog_digest": snapshot.digest,
        "generation": snapshot.data["generation"],
        "approved": snapshot.approved,
        "auto_promote": snapshot.data["auto_promote"],
        "required_targets": list(snapshot.data["required_targets"]),
        "components": len(snapshot.data["components"]),
        "excluded_components": sorted(excluded),
        "manual_components": sorted(
            key for key, entry in snapshot.data["components"].items() if entry["kind"] == "external"
        ),
        "ready": not missing,
        "blockers": missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--require-ready", action="store_true")
    arguments = parser.parse_args()
    try:
        result = readiness(arguments.catalog)
    except CatalogError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 2 if arguments.require_ready and not result["ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
