"""Reject incomplete compatibility evidence without changing approved catalog data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from software_catalog import _UNVERSIONED, CatalogError, CatalogSnapshot, resolve_entry

SCENARIOS = frozenset({"fresh", "upgrade", "repeat", "failure-safety"})
CHECKS = frozenset({"product-gates", "behavior", "updater-controls"})
_RECEIPT = frozenset(
    {
        "catalog_digest",
        "sources",
        "target",
        "run_id",
        "runner_image",
        "scenarios",
        "checks",
        "observations",
    }
)


class QualificationError(CatalogError):
    """Evidence cannot authorize a catalog promotion."""


def _successes(value: Any, expected: frozenset[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise QualificationError("required checks or scenarios are missing or unexplained")
    if any(status != "success" for status in value.values()):
        raise QualificationError("a required qualification result did not succeed")


def observation_targets(snapshot: CatalogSnapshot, target: str) -> dict[str, tuple[str, str]]:
    """Map each required observation to its declared component and actual host artifact."""
    expected = {}
    system, architecture = target.rsplit("-", 1)
    for key, entry in snapshot.data["components"].items():
        if entry["kind"] in _UNVERSIONED:
            continue
        if entry["kind"] != "extension":
            expected[key] = (key, target)
            continue
        declared = entry["discovery"].get("hosts", "")
        if not declared:
            raise QualificationError("extension qualification lacks declared host coverage")
        hosts = set(declared.split(","))
        if "windows" in hosts and system in {"windows", "wsl2"}:
            expected[key + "@windows"] = (key, "windows-" + architecture)
        if "wsl" in hosts and system in {"linux", "wsl2"}:
            expected[key + "@wsl"] = (key, target)
    return expected


def qualification_components(snapshot: CatalogSnapshot) -> set[str]:
    """Select components whose declared hosts occur in the rollout matrix."""
    return {
        component
        for target in snapshot.data["required_targets"]
        for component, _artifact_target in observation_targets(snapshot, target).values()
    }


def _observations(snapshot: CatalogSnapshot, receipt: Mapping[str, Any]) -> None:
    observations = receipt["observations"]
    expected = observation_targets(snapshot, receipt["target"])
    if not isinstance(observations, dict) or set(observations) != set(expected):
        raise QualificationError("installed component evidence does not cover the inventory")
    for key, observation in observations.items():
        component, artifact_target = expected[key]
        artifact = resolve_entry(snapshot, component, artifact_target, require_approved=False)
        if not isinstance(observation, dict) or set(observation) != {
            "version",
            "integrity",
            "owned",
            "managed_path",
        }:
            raise QualificationError("invalid installed observation")
        version = artifact.get("revision", artifact.get("version"))
        integrity = artifact.get("digest", artifact.get("revision"))
        if (
            observation["version"] != version
            or observation["integrity"] != integrity
            or observation["owned"] is not True
            or observation["managed_path"] is not True
        ):
            raise QualificationError(f"installed observation differs from the candidate: {key}")


def validate_results(
    snapshot: CatalogSnapshot,
    results: Sequence[Mapping[str, Any]],
    *,
    expected_sources: Mapping[str, str],
    run_id: str,
) -> dict[str, Any]:
    """Validate controller-collected receipts; API job conclusions remain a separate gate."""
    if not run_id or dict(snapshot.data["sources"]) != dict(expected_sources):
        raise QualificationError("qualification source revisions moved")
    required = set(snapshot.data["required_targets"])
    if not qualification_components(snapshot):
        raise QualificationError("required platform matrix has no versioned component coverage")
    if len(results) != len(required):
        raise QualificationError("missing or duplicate required target result")
    observed: set[str] = set()
    for receipt in results:
        if set(receipt) != _RECEIPT:
            raise QualificationError("qualification receipt schema mismatch")
        target = receipt["target"]
        if target not in required or target in observed:
            raise QualificationError("missing, duplicate or unsupported qualification target")
        observed.add(target)
        if (
            receipt["catalog_digest"] != snapshot.digest
            or receipt["sources"] != dict(expected_sources)
            or receipt["run_id"] != run_id
            or not isinstance(receipt["runner_image"], str)
            or not receipt["runner_image"]
        ):
            raise QualificationError("qualification receipt identity mismatch")
        _successes(receipt["scenarios"], SCENARIOS)
        _successes(receipt["checks"], CHECKS)
        _observations(snapshot, receipt)
    if observed != required:
        raise QualificationError("required platform coverage is incomplete")
    return {
        "schema_version": 1,
        "catalog_digest": snapshot.digest,
        "sources": dict(expected_sources),
        "required_targets": sorted(required),
        "run_id": run_id,
    }
