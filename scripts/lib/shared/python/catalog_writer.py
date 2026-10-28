"""Submit verified approval data through a new branch and a reviewed development PR."""

from __future__ import annotations

import base64
import binascii
import hashlib
from urllib.parse import quote

from catalog_github import PREFIX, REPOSITORY, GitHubError
from catalog_publication import _object, _sha, plan_publication, read_commit_tree
from software_catalog import CatalogError


def _scope(api, repository_id):
    scope = _object(api.json("installation/repositories?per_page=100&page=1"))
    repositories = scope.get("repositories")
    if (
        type(scope.get("total_count")) is not int
        or scope["total_count"] != 1
        or not isinstance(repositories, list)
        or len(repositories) != 1
        or not isinstance(repositories[0], dict)
        or repositories[0].get("id") != repository_id
        or repositories[0].get("full_name") != REPOSITORY
    ):
        raise CatalogError("publisher needs an installation token scoped to only its repository")


def _approval_tree(plan, candidate_tree):
    files = plan["files"]
    if not isinstance(files, list) or len(files) != 2:
        raise CatalogError("writer accepts only the two generated approval files")
    expected, entries, seen = dict(candidate_tree), [], set()
    for item in files:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "mode", "sha256", "content_base64"}
            or item["path"] not in {"catalog/approval", "catalog/approval.json"}
            or item["path"] in seen
            or item["mode"] != "100644"
        ):
            raise CatalogError("writer received an unsafe or duplicate approval path")
        try:
            raw = base64.b64decode(item["content_base64"], validate=True)
            text = raw.decode("utf-8")
        except (ValueError, TypeError, UnicodeError, binascii.Error) as error:
            raise CatalogError("invalid generated approval encoding") from error
        if len(raw) > 4_194_304 or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise CatalogError("generated approval bytes differ from their verified plan")
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        expected[item["path"]] = ("100644", blob)
        entries.append({"path": item["path"], "mode": "100644", "type": "blob", "content": text})
        seen.add(item["path"])
    return expected, entries


def _reference(api, branch):
    refs = api.json(f"{PREFIX}/git/matching-refs/heads/{branch}")
    if not isinstance(refs, list) or len(refs) > 1:
        raise CatalogError("publication reference is duplicated or ambiguous")
    if not refs:
        return None
    ref = _object(refs[0])
    obj = _object(ref.get("object"))
    if ref.get("ref") != "refs/heads/" + branch or obj.get("type") != "commit":
        raise CatalogError("publication reference differs from its exact transaction")
    return _sha(obj.get("sha"))


def _commit(api, sha, parent, expected_tree, message):
    commit, tree = read_commit_tree(api, sha)
    parents, verification = commit.get("parents"), _object(commit.get("verification"))
    if (
        not isinstance(parents, list)
        or len(parents) != 1
        or not isinstance(parents[0], dict)
        or parents[0].get("sha") != parent
        or commit.get("message") != message
        or tree != expected_tree
    ):
        raise CatalogError("publication commit differs from its tested parent or approval tree")
    if verification.get("verified") is not True or verification.get("reason") != "valid":
        raise CatalogError("publication commit needs a verified bot signature")


def _pull_request(
    api, branch, commit, repository_id, base, *, base_branch="development", ignore_number=None
):
    head = quote(REPOSITORY.split("/", 1)[0] + ":" + branch, safe="")
    pulls = api.json(f"{PREFIX}/pulls?state=all&head={head}&per_page=100&page=1")
    if not isinstance(pulls, list) or len(pulls) > 100:
        raise CatalogError("publication PR listing is incomplete")
    if ignore_number is not None:
        ignored = [
            pull for pull in pulls if isinstance(pull, dict) and pull.get("number") == ignore_number
        ]
        if len(ignored) != 1:
            raise CatalogError("reviewed development PR is missing or duplicated in the listing")
        pulls = [pull for pull in pulls if pull not in ignored]
    if len(pulls) > 1:
        raise CatalogError("publication PR is duplicated or its listing is incomplete")
    if not pulls:
        return None
    pull = _object(pulls[0])
    number = pull.get("number")
    if (
        type(number) is not int
        or number <= 0
        or pull.get("html_url") != f"https://github.com/{REPOSITORY}/pull/{number}"
        or pull.get("state") != "open"
        or pull.get("merged_at") is not None
    ):
        raise CatalogError("publication PR was closed, merged or changed")
    for key, ref, sha in (("head", branch, commit), ("base", base_branch, base)):
        side = _object(pull.get(key))
        if (
            side.get("ref") != ref
            or side.get("sha") != sha
            or _object(side.get("repo")).get("id") != repository_id
        ):
            raise CatalogError("publication PR repository, head or base moved")
    return {"number": number, "url": pull["html_url"]}


def submit_publication(api, identity, **policy):
    """Read fresh evidence, then submit data only; never advance a protected branch."""
    if not policy.get("publisher_app_slug"):
        raise CatalogError("submission requires the App slug from trusted controller configuration")
    _scope(api, identity.repository_id)
    plan = plan_publication(api, identity, **policy)
    transaction = plan["transaction_id"]
    branch = "catalog-publish/" + transaction
    message = (
        "chore(catalog): qualify "
        + plan["catalog_digest"]
        + "\n\nCatalog-Transaction: "
        + transaction
    )
    candidate, candidate_tree = read_commit_tree(api, identity.candidate)
    expected_tree, entries = _approval_tree(plan, candidate_tree)

    def fresh():
        if plan_publication(api, identity, **policy)["transaction_id"] != transaction:
            raise CatalogError("publication evidence or policy changed during submission")

    commit = _reference(api, branch)
    if commit is None:
        tree = _object(
            api.post(
                f"{PREFIX}/git/trees",
                {"base_tree": _object(candidate.get("tree"))["sha"], "tree": entries},
            )
        )
        created = _object(
            api.post(
                f"{PREFIX}/git/commits",
                {
                    "tree": _sha(tree.get("sha")),
                    "parents": [identity.candidate],
                    "message": message,
                },
            )
        )
        commit = _sha(created.get("sha"))
        _commit(api, commit, identity.candidate, expected_tree, message)
        fresh()
        try:
            api.post(f"{PREFIX}/git/refs", {"ref": "refs/heads/" + branch, "sha": commit})
        except GitHubError:
            # A timed-out request may have created the reference. Never retry blindly.
            if _reference(api, branch) != commit:
                raise
    _commit(api, commit, identity.candidate, expected_tree, message)
    if _reference(api, branch) != commit:
        raise CatalogError("publication branch moved during submission")
    fresh()
    base = plan["expected_heads"]["shared-development"]
    pull = _pull_request(api, branch, commit, identity.repository_id, base)
    if pull is None:
        fresh()
        if _reference(api, branch) != commit:
            raise CatalogError("publication branch moved before PR creation")
        try:
            api.post(
                f"{PREFIX}/pulls",
                {
                    "base": "development",
                    "head": branch,
                    "title": "chore(catalog): approve tested " + plan["catalog_digest"][:20],
                    "body": "Catalog transaction: "
                    + transaction
                    + "\n\nFull qualification run: "
                    + f"https://github.com/{REPOSITORY}/actions/runs/{identity.run_id}"
                    + "\n\nReview is required. Main publication remains held until the development merge is verified.",
                    "maintainer_can_modify": False,
                    "draft": False,
                },
            )
        except GitHubError:
            if _pull_request(api, branch, commit, identity.repository_id, base) is None:
                raise
        pull = _pull_request(api, branch, commit, identity.repository_id, base)
        if pull is None:
            raise CatalogError("publication PR creation was not confirmed")
    fresh()
    if _reference(api, branch) != commit:
        raise CatalogError("publication branch moved after PR creation")
    if _pull_request(api, branch, commit, identity.repository_id, base) != pull:
        raise CatalogError("publication PR changed during final verification")
    return {
        "schema_version": 1,
        "status": "submitted",
        "published": False,
        "transaction_id": transaction,
        "branch": branch,
        "commit": commit,
        "pull_request": pull,
        "main_phase": "held-until-reviewed-development-merge",
    }
