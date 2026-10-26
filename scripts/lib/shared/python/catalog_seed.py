"""Prepare unapproved discovery-tool pins for the first catalog qualification."""

from __future__ import annotations

from dataclasses import replace

from catalog_discovery import DiscoveryError, _mutable, build_candidate
from catalog_promotion import verify_candidate_policy
from software_catalog import _validate

TOOLS = frozenset({"nodejs", "npm", "uv"})


def _dependencies(components):
    required, pending = set(), list(TOOLS)
    while pending:
        key = pending.pop()
        if key in required:
            continue
        if key not in components:
            raise DiscoveryError("initial catalog lacks required discovery tooling: " + key)
        required.add(key)
        pending.extend(components[key]["requires"])
    return required


def build_bootstrap(snapshot, *, fetch, runtime_resolver):
    """Resolve tooling only; keep the full inventory and never claim approval."""
    if snapshot.approved or snapshot.data["auto_promote"]:
        raise DiscoveryError("bootstrap discovery is only for an unapproved, disabled seed")
    if tuple(snapshot.data["required_targets"]) != ("linux-amd64",):
        raise DiscoveryError("initial bootstrap currently qualifies Linux AMD64 only")
    data = _mutable(snapshot.data)
    required = _dependencies(data["components"])
    subset = {**data, "components": {key: data["components"][key] for key in sorted(required)}}
    resolved = build_candidate(
        replace(snapshot, data=subset, approved=False),
        fetch=fetch,
        runtime_resolver=runtime_resolver,
    )
    for key in required:
        data["components"][key] = resolved["components"][key]
    _validate(data)
    verify_candidate_policy(_mutable(snapshot.data), data)
    return data


def verify_bootstrap_tools(bootstrap, candidate):
    """Hold a seed if metadata changed after its discovery tools were installed."""
    required = _dependencies(bootstrap["components"])
    for key in required:
        if _mutable(bootstrap["components"][key]) != _mutable(candidate["components"].get(key)):
            raise DiscoveryError(
                f"discovery tool pins changed while preparing the initial seed: {key}"
            )
