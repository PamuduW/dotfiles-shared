"""Catalog-only compare-and-swap preconditions for the trusted writer."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence, Set
from typing import Any


class PromotionError(ValueError):
    """Promotion must hold without changing stable catalog data."""


_DATA = re.compile(
    r"catalog/(?:software\.(?:json|tsv)|approval(?:\.json)?|locks/[A-Za-z0-9_./-]+)\Z"
)
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_HEADS = frozenset({"agentbot", "dotfiles", "shared-main", "shared-development"})


def verify_promotion(
    *,
    expected_heads: Mapping[str, str],
    current_heads: Mapping[str, str],
    candidate_commit: str,
    run_head: str,
    expected_workflow: str,
    run_workflow: str,
    required_jobs: Set[str],
    jobs: Sequence[Mapping[str, Any]],
    changed_files: Mapping[str, str],
    auto_promote: bool,
) -> None:
    """Validate live API/Git observations; callers must reread heads immediately before writes."""
    if auto_promote is not True:
        raise PromotionError("automatic promotion is disabled")
    if (
        set(expected_heads) != _HEADS
        or dict(current_heads) != dict(expected_heads)
        or any(
            not isinstance(value, str) or not _SHA.fullmatch(value)
            for value in expected_heads.values()
        )
        or expected_heads["shared-main"] != expected_heads["shared-development"]
    ):
        raise PromotionError("source/base heads moved or development contains unrelated work")
    if (
        not _SHA.fullmatch(candidate_commit)
        or candidate_commit != run_head
        or not _SHA.fullmatch(expected_workflow)
        or expected_workflow != run_workflow
    ):
        raise PromotionError("qualification ran different candidate/workflow code")
    if not required_jobs:
        raise PromotionError("required qualification job set cannot be empty")
    found: dict[str, str] = {}
    for job in jobs:
        name = job.get("name")
        if not isinstance(name, str) or name not in required_jobs:
            continue
        if name in found or job.get("conclusion") != "success":
            raise PromotionError(
                "required qualification job failed, skipped, cancelled or duplicated"
            )
        found[name] = "success"
    if set(found) != required_jobs:
        raise PromotionError("a required qualification job result is missing")
    if not changed_files:
        raise PromotionError("no catalog change to promote")
    for path, mode in changed_files.items():
        if (
            not _DATA.fullmatch(path)
            or "/../" in path
            or "/./" in path
            or "//" in path
            or path.endswith(("/..", "/.", "/"))
            or mode != "100644"
        ):
            raise PromotionError(
                "automatic candidate changes are restricted to regular catalog data"
            )


def verify_candidate_policy(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
    """Automation may change versions, not the policy authorizing those changes."""
    for field in ("schema_version", "required_targets", "auto_promote"):
        if baseline[field] != candidate[field]:
            raise PromotionError("candidate changed catalog coverage or promotion policy")
    previous = baseline["components"]
    proposed = candidate["components"]
    if set(previous) != set(proposed):
        raise PromotionError("automatic candidate changed the declared component inventory")
    for identity, entry in previous.items():
        for field in ("owner", "kind", "source", "discovery", "requires", "blockers"):
            if entry[field] != proposed[identity][field]:
                raise PromotionError(
                    f"automatic candidate changed component policy: {identity}/{field}"
                )
        if set(entry["targets"]) - set(proposed[identity]["targets"]):
            raise PromotionError("automatic candidate narrowed supported artifact targets")
