"""Propose identical tested catalog bytes to main after a verified development merge."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from catalog_github import PREFIX, GitHubError
from catalog_publication import (
    _object,
    _plan_publication,
    _sha,
    read_commit_tree,
    read_heads,
    read_protections,
    validate_ancestor,
)
from catalog_writer import _approval_tree, _commit, _pull_request, _reference, _scope
from software_catalog import CatalogError


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise CatalogError("review or merge timestamp is missing or invalid")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CatalogError("review or merge timestamp is invalid") from error


def _merged_pull(api, number, repository_id):
    if type(number) is not int or number <= 0:
        raise CatalogError("main publication requires an explicit development PR number")
    pull = _object(api.json(f"{PREFIX}/pulls/{number}"))
    if (
        pull.get("number") != number
        or pull.get("state") != "closed"
        or pull.get("merged") is not True
        or _timestamp(pull.get("merged_at")) > datetime.now(timezone.utc)
        or type(_object(pull.get("user")).get("id")) is not int
    ):
        raise CatalogError("development PR has not been verified as merged")
    head, base = _object(pull.get("head")), _object(pull.get("base"))
    if (
        base.get("ref") != "development"
        or _object(base.get("repo")).get("id") != repository_id
        or _object(head.get("repo")).get("id") != repository_id
        or not isinstance(head.get("ref"), str)
        or not re.fullmatch(r"catalog-publish/[0-9a-f]{64}", head["ref"])
    ):
        raise CatalogError("reviewed development PR repository or branch differs")
    _sha(head.get("sha"))
    _sha(pull.get("merge_commit_sha"))
    return pull


def _human_reviews(api, pull, required):
    latest, seen = {}, set()
    for page in range(1, 21):
        reviews = api.json(f"{PREFIX}/pulls/{pull['number']}/reviews?per_page=100&page={page}")
        if not isinstance(reviews, list) or len(reviews) > 100:
            raise CatalogError("development review listing is incomplete")
        for review in reviews:
            review = _object(review)
            identifier, state = review.get("id"), review.get("state")
            if type(identifier) is not int or identifier <= 0 or identifier in seen:
                raise CatalogError("development reviews have invalid or duplicate identities")
            seen.add(identifier)
            if state not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED", "COMMENTED", "PENDING"}:
                raise CatalogError("development review state is unknown")
            if state in {"COMMENTED", "PENDING"}:
                continue
            user = _object(review.get("user"))
            if user.get("type") != "User":
                continue
            if type(user.get("id")) is not int or user["id"] <= 0:
                raise CatalogError("human review identity is invalid")
            latest[user["id"]] = review
        if len(reviews) < 100:
            break
    else:
        raise CatalogError("development review listing exceeds its bound")
    approvals = 0
    for user_id, review in latest.items():
        if (
            review["state"] != "APPROVED"
            or review.get("commit_id") != pull["head"]["sha"]
            or user_id == pull["user"]["id"]
            or _timestamp(review.get("submitted_at")) > _timestamp(pull["merged_at"])
        ):
            continue
        login = review["user"].get("login")
        if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", login):
            raise CatalogError("human reviewer login is invalid")
        permission = _object(api.json(f"{PREFIX}/collaborators/{login}/permission"))
        if _object(permission.get("user")).get("id") == user_id and permission.get(
            "permission"
        ) in {"write", "admin"}:
            approvals += 1
    if approvals < required:
        raise CatalogError("development merge lacks current human approval of the publication head")


def plan_main_publication(api, identity, *, development_pull_request, **policy):
    pull = _merged_pull(api, development_pull_request, identity.repository_id)
    merged = pull["merge_commit_sha"]
    plan = _plan_publication(api, identity, _reviewed_development=merged, **policy)
    branch = "catalog-publish/" + plan["transaction_id"]
    if pull["head"]["ref"] != branch:
        raise CatalogError("development merge belongs to another catalog transaction")
    commit = pull["head"]["sha"]
    _, candidate_tree = read_commit_tree(api, identity.candidate)
    expected_tree, _ = _approval_tree(plan, candidate_tree)
    message = (
        "chore(catalog): qualify "
        + plan["catalog_digest"]
        + "\n\nCatalog-Transaction: "
        + plan["transaction_id"]
    )
    _commit(api, commit, identity.candidate, expected_tree, message)
    if _reference(api, branch) != commit:
        raise CatalogError("reviewed publication branch moved")
    _, merge_tree = read_commit_tree(api, merged)
    if merge_tree != expected_tree:
        raise CatalogError("reviewed development merge changed the tested publication tree")
    validate_ancestor(api, policy["expected_heads"]["shared-development"], merged)
    owner_type = _object(_object(api.json(PREFIX)).get("owner")).get("type")
    protection = read_protections(api, policy["publisher_app_id"], owner_type)
    required = protection["development"]["required_pull_request_reviews"][
        "required_approving_review_count"
    ]
    _human_reviews(api, pull, required)
    expected_live = {**policy["expected_heads"], "shared-development": merged}
    if read_heads(api) != expected_live:
        raise CatalogError("source or base heads moved during review verification")
    if read_protections(api, policy["publisher_app_id"], owner_type) != protection:
        raise CatalogError("branch protection changed during review verification")
    if _merged_pull(api, development_pull_request, identity.repository_id) != pull:
        raise CatalogError("development PR evidence changed during main planning")
    return {
        "schema_version": 1,
        "status": "planned-main",
        "published": False,
        "transaction_id": plan["transaction_id"],
        "catalog_digest": plan["catalog_digest"],
        "branch": branch,
        "commit": commit,
        "development_pull_request": development_pull_request,
        "development_merge": merged,
        "expected_main": plan["expected_heads"]["shared-main"],
    }


def submit_main_publication(api, identity, *, development_pull_request, **policy):
    if not policy.get("publisher_app_slug"):
        raise CatalogError("submission requires the App slug from trusted controller configuration")
    _scope(api, identity.repository_id)
    plan = plan_main_publication(
        api, identity, development_pull_request=development_pull_request, **policy
    )
    branch, commit, base = plan["branch"], plan["commit"], plan["expected_main"]

    def fresh():
        current = plan_main_publication(
            api, identity, development_pull_request=development_pull_request, **policy
        )
        if current != plan:
            raise CatalogError("main publication evidence changed during submission")

    def pull_request():
        return _pull_request(
            api,
            branch,
            commit,
            identity.repository_id,
            base,
            base_branch="main",
            ignore_number=development_pull_request,
        )

    pull = pull_request()
    if pull is None:
        fresh()
        if _reference(api, branch) != commit:
            raise CatalogError("reviewed publication branch moved before main PR creation")
        try:
            api.post(
                f"{PREFIX}/pulls",
                {
                    "base": "main",
                    "head": branch,
                    "title": "chore(catalog): publish tested " + plan["catalog_digest"][:20],
                    "body": "Catalog transaction: "
                    + plan["transaction_id"]
                    + f"\n\nReviewed development PR: https://github.com/PamuduW/dotfiles-shared/pull/{development_pull_request}"
                    + "\n\nThis PR contains the identical tested catalog and approval bytes. Main review is required.",
                    "maintainer_can_modify": False,
                    "draft": False,
                },
            )
        except GitHubError:
            if pull_request() is None:
                raise
        pull = pull_request()
        if pull is None:
            raise CatalogError("main publication PR creation was not confirmed")
    fresh()
    if _reference(api, branch) != commit:
        raise CatalogError("reviewed publication branch moved after main PR creation")
    if pull_request() != pull:
        raise CatalogError("main PR changed during final verification")
    return {
        "schema_version": 1,
        "status": "submitted-main",
        "published": False,
        "transaction_id": plan["transaction_id"],
        "branch": branch,
        "commit": commit,
        "pull_request": pull,
        "development_merge": plan["development_merge"],
        "main_phase": "held-until-reviewed-main-merge",
    }
