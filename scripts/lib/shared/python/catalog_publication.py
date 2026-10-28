"""Plan catalog-only publication from live evidence without any remote writes."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import tempfile
from pathlib import Path

from catalog_github import (
    PREFIX,
    REPOSITORY,
    SHA,
    GitHubError,
    RunIdentity,
    decode_json,
    read_qualification,
    validate_run,
)
from catalog_promotion import PromotionError, verify_candidate_policy
from catalog_qualification import validate_results
from software_catalog import CatalogError, _locks, _read, load_snapshot

_HEADS = {
    "agentbot": ("agentbot", "main"),
    "dotfiles": ("dotfiles", "main"),
    "shared-main": ("dotfiles-shared", "main"),
    "shared-development": ("dotfiles-shared", "development"),
}
_DATA = re.compile(
    r"catalog/(?:software\.(?:json|tsv)|approval(?:\.json)?|locks/[A-Za-z0-9_./-]+)\Z"
)
_MAX_BLOB = 4_194_304
_MAX_CATALOG = 33_554_432


def load_policy(path):
    """Read operator-reviewed controller inputs, never executable artifact contents."""
    policy = decode_json(_read(path, 65_536))
    if (
        not isinstance(policy, dict)
        or not {
            "schema_version",
            "identity",
            "expected_heads",
            "expected_sources",
            "required_jobs",
            "publisher_app_id",
        }
        <= set(policy)
        or not set(policy)
        <= {
            "schema_version",
            "identity",
            "expected_heads",
            "expected_sources",
            "required_jobs",
            "publisher_app_id",
            "publisher_app_slug",
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
    return RunIdentity(**identity), policy


def _sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise CatalogError("Git object identity must be a full SHA")
    return value


def _object(value):
    if not isinstance(value, dict):
        raise CatalogError("GitHub object response is missing or invalid")
    return value


def _path(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise CatalogError("unsafe Git tree path")
    return value


def _data_path(path):
    return bool(_DATA.fullmatch(_path(path)))


def read_heads(api):
    heads = {}
    for key, (repository, branch) in _HEADS.items():
        ref = _object(api.json(f"repos/PamuduW/{repository}/git/ref/heads/{branch}"))
        value = _object(ref.get("object"))
        if ref.get("ref") != "refs/heads/" + branch or value.get("type") != "commit":
            raise CatalogError("source or base ref is not the expected branch commit")
        heads[key] = _sha(value.get("sha"))
    return heads


def validate_candidate_ref(api, identity):
    ref = _object(api.json(f"{PREFIX}/git/ref/heads/{identity.branch}"))
    value = _object(ref.get("object"))
    if (
        ref.get("ref") != "refs/heads/" + identity.branch
        or value.get("type") != "commit"
        or value.get("sha") != identity.candidate
    ):
        raise CatalogError("candidate branch moved from the tested commit")


def validate_protection(protection, publisher_app_id, owner_type):
    if not isinstance(owner_type, str) or owner_type not in {"User", "Organization"}:
        raise CatalogError("repository owner type is missing or unsupported")
    protection = _object(protection)
    checks = _object(protection.get("required_status_checks"))
    reviews = _object(protection.get("required_pull_request_reviews"))
    count = reviews.get("required_approving_review_count")
    if (
        checks.get("strict") is not True
        or type(count) is not int
        or count < 1
        or reviews.get("dismiss_stale_reviews") is not True
        or reviews.get("require_last_push_approval") is not True
    ):
        raise CatalogError("strict checks and current human review are required")
    required = checks.get("checks")
    if not isinstance(required, list) or not all(isinstance(item, dict) for item in required):
        raise CatalogError("required status check sources are missing")
    matches = [item for item in required if item.get("context") == "catalog/publication-ready"]
    if len(matches) != 1 or matches[0].get("app_id") != publisher_app_id:
        raise CatalogError("publication status must be required from the trusted App")
    allowances = reviews.get("bypass_pull_request_allowances")
    if allowances is not None or owner_type != "User":
        allowances = _object(allowances)
        if any(allowances.get(kind) != [] for kind in ("users", "teams", "apps")):
            raise CatalogError("review bypass is not permitted")
    for key, enabled in (
        ("enforce_admins", True),
        ("allow_force_pushes", False),
        ("allow_deletions", False),
    ):
        if _object(protection.get(key)).get("enabled") is not enabled:
            raise CatalogError("branch protection permits an unsafe bypass or ref update")
    restrictions = protection.get("restrictions")
    if owner_type == "User":
        if restrictions is not None:
            raise CatalogError("personal repository reported unsupported push restrictions")
        return
    restrictions = _object(restrictions)
    apps = restrictions.get("apps")
    if (
        restrictions.get("users") != []
        or restrictions.get("teams") != []
        or not isinstance(apps, list)
        or len(apps) != 1
        or not isinstance(apps[0], dict)
        or apps[0].get("id") != publisher_app_id
    ):
        raise CatalogError("protected branch updates need one non-bypassing publisher App")


def read_protections(api, publisher_app_id, owner_type):
    if api.json(f"{PREFIX}/rulesets") != []:
        raise CatalogError("ruleset-based publication policy requires separate verification")
    protections = {}
    for branch in ("main", "development"):
        protections[branch] = api.json(f"{PREFIX}/branches/{branch}/protection")
        validate_protection(protections[branch], publisher_app_id, owner_type)
    return protections


def read_commit_tree(api, revision):
    commit = _object(api.json(f"{PREFIX}/git/commits/{_sha(revision)}"))
    if commit.get("sha") != revision:
        raise CatalogError("Git commit identity differs")
    tree_sha = _sha(_object(commit.get("tree")).get("sha"))
    tree = _object(api.json(f"{PREFIX}/git/trees/{tree_sha}?recursive=1"))
    entries = tree.get("tree")
    if (
        tree.get("sha") != tree_sha
        or tree.get("truncated") is not False
        or not isinstance(entries, list)
        or len(entries) > 20_000
    ):
        raise CatalogError("Git tree is incomplete or outside bounds")
    files, seen = {}, set()
    for item in entries:
        item = _object(item)
        path = _path(item.get("path"))
        if path in seen:
            raise CatalogError("duplicate Git tree entry")
        seen.add(path)
        sha = _sha(item.get("sha"))
        mode, kind = item.get("mode"), item.get("type")
        if kind == "tree" and mode == "040000":
            continue
        if (kind, mode) not in {
            ("blob", "100644"),
            ("blob", "100755"),
            ("blob", "120000"),
            ("commit", "160000"),
        }:
            raise CatalogError("invalid Git tree entry type or mode")
        files[path] = (mode, sha)
    return commit, files


def validate_ancestor(api, ancestor, head):
    if ancestor == head:
        return
    comparison = _object(api.json(f"{PREFIX}/compare/{_sha(ancestor)}...{_sha(head)}"))
    if (
        comparison.get("status") != "ahead"
        or _object(comparison.get("merge_base_commit")).get("sha") != ancestor
    ):
        raise CatalogError("development or shared source is not integrated into main")


def validate_trees(base, development, candidate, source):
    if base != development:
        raise CatalogError("development contains unrelated or unpublished data")
    for tree in (base, candidate, source):
        for path, (mode, _sha_value) in tree.items():
            if _data_path(path) and mode != "100644":
                raise CatalogError("catalog contains nonregular data")

    def outside(tree):
        return {path: value for path, value in tree.items() if not _data_path(path)}

    if outside(base) != outside(candidate) or outside(base) != outside(source):
        raise CatalogError("candidate or frozen source differs outside catalog data")
    changed = {
        path for path in base.keys() | candidate.keys() if base.get(path) != candidate.get(path)
    }
    if not changed or any(not _data_path(path) for path in changed):
        raise CatalogError("candidate must contain only catalog data changes")
    if "catalog/approval" in candidate or "catalog/approval.json" in candidate:
        raise CatalogError("candidate cannot supply its own approval")


def read_catalog(api, tree, directory, *, approved):
    files, total = {}, 0
    for path, (mode, sha) in sorted(tree.items()):
        if not _data_path(path):
            continue
        if not _data_path(path) or mode != "100644":
            raise CatalogError("unsafe catalog Git object")
        blob = _object(api.json(f"{PREFIX}/git/blobs/{sha}"))
        size, content = blob.get("size"), blob.get("content")
        if (
            blob.get("sha") != sha
            or blob.get("encoding") != "base64"
            or type(size) is not int
            or not 0 <= size <= _MAX_BLOB
            or not isinstance(content, str)
            or len(content) > 2 * _MAX_BLOB
        ):
            raise CatalogError("catalog blob identity, encoding or size differs")
        try:
            raw = base64.b64decode(content.replace("\n", ""), validate=True)
        except (ValueError, binascii.Error) as error:
            raise CatalogError("invalid catalog blob encoding") from error
        digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        total += len(raw)
        if len(raw) != size or digest != sha or total > _MAX_CATALOG:
            raise CatalogError("catalog Git blob bytes differ or exceed limits")
        relative = path.removeprefix("catalog/")
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        files[relative] = raw
    snapshot = load_snapshot(directory, require_approved=approved)
    expected = {"software.json", "software.tsv", *_locks(snapshot.data)}
    if approved:
        expected.update({"approval", "approval.json"})
    if set(files) != expected:
        raise CatalogError("catalog contains unreferenced files or missing proof")
    return snapshot, files


def _publisher_origin(api, identity, slug, candidate_commit):
    if slug is None:
        return
    if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", slug):
        raise CatalogError("trusted publisher App slug is invalid")
    for suffix in ("", f"/attempts/{identity.attempt}"):
        actor = _object(
            _object(api.json(f"{PREFIX}/actions/runs/{identity.run_id}{suffix}")).get("actor")
        )
        if actor.get("type") != "Bot" or actor.get("login") != slug + "[bot]":
            raise CatalogError(
                "candidate qualification was not initiated by the trusted publisher App"
            )
    verification = _object(candidate_commit.get("verification"))
    if verification.get("verified") is not True or verification.get("reason") != "valid":
        raise CatalogError(
            "candidate commit needs a verified signature from trusted App preparation"
        )


def _plan_publication(
    api,
    identity,
    *,
    expected_heads,
    expected_sources,
    required_jobs,
    publisher_app_id,
    publisher_app_slug=None,
    _reviewed_development=None,
):
    """Build a data-bound submission plan without changing remote or caller files."""
    if (
        not isinstance(expected_heads, dict)
        or set(expected_heads) != set(_HEADS)
        or not isinstance(expected_sources, dict)
        or set(expected_sources) != {"agentbot", "dotfiles", "dotfiles-shared"}
        or type(publisher_app_id) is not int
        or publisher_app_id <= 0
    ):
        raise CatalogError("publication needs explicit trusted heads, sources and App identity")
    for value in (*expected_heads.values(), *expected_sources.values()):
        _sha(value)
    if any(expected_sources[name] != expected_heads[name] for name in ("agentbot", "dotfiles")):
        raise CatalogError("product source revisions differ from expected live heads")
    repository = _object(api.json(PREFIX))
    if (
        repository.get("id") != identity.repository_id
        or repository.get("full_name") != REPOSITORY
        or repository.get("default_branch") != "main"
    ):
        raise CatalogError("publication repository authority differs")
    live_heads = dict(expected_heads)
    if _reviewed_development is not None:
        live_heads["shared-development"] = _sha(_reviewed_development)
    if read_heads(api) != live_heads:
        raise CatalogError("source or base heads moved")
    validate_candidate_ref(api, identity)
    owner_type = _object(repository.get("owner")).get("type")
    protections = read_protections(api, publisher_app_id, owner_type)
    main, development = expected_heads["shared-main"], expected_heads["shared-development"]
    validate_ancestor(api, development, main)
    validate_ancestor(api, expected_sources["dotfiles-shared"], main)
    _, base_tree = read_commit_tree(api, main)
    _, development_tree = read_commit_tree(api, development)
    candidate_commit, candidate_tree = read_commit_tree(api, identity.candidate)
    _publisher_origin(api, identity, publisher_app_slug, candidate_commit)
    _, source_tree = read_commit_tree(api, expected_sources["dotfiles-shared"])
    parents = candidate_commit.get("parents")
    if (
        not isinstance(parents, list)
        or len(parents) != 1
        or not isinstance(parents[0], dict)
        or parents[0].get("sha") != main
    ):
        raise CatalogError("candidate does not have the exact frozen main parent")
    validate_trees(base_tree, development_tree, candidate_tree, source_tree)
    with tempfile.TemporaryDirectory(prefix="catalog-publication-") as temporary:
        directory = Path(temporary)
        baseline, _ = read_catalog(api, base_tree, directory / "baseline", approved=True)
        candidate, files = read_catalog(
            api, candidate_tree, directory / "candidate", approved=False
        )
        if not baseline.data["auto_promote"] or not candidate.data["auto_promote"]:
            raise CatalogError("automatic promotion is disabled; manual seed is not supported")
        if dict(candidate.data["sources"]) != expected_sources or tuple(
            candidate.data["required_targets"]
        ) != ("linux-amd64",):
            raise CatalogError("candidate sources or rollout matrix differ from trusted policy")
        try:
            verify_candidate_policy(baseline.data, candidate.data)
        except PromotionError as error:
            raise CatalogError(str(error)) from error
        if candidate.digest == baseline.digest:
            raise CatalogError("no changed catalog to plan")
        predecessor = {"commit": main, "catalog_digest": baseline.digest}
        receipts, evidence = read_qualification(
            api, identity, required_jobs, candidate.data["required_targets"], predecessor
        )
        qualified = validate_results(
            candidate, receipts, expected_sources=expected_sources, run_id=str(identity.run_id)
        )
        proof = {
            "schema_version": 1,
            "profile": "full-qualification",
            "qualification": qualified,
            "evidence": evidence,
            "expected_heads": dict(expected_heads),
            "approved_predecessor": predecessor,
        }
        receipt = (json.dumps(proof, sort_keys=True, indent=2) + "\n").encode()
        marker = (
            "\n".join(
                (
                    "software-approved-v1",
                    candidate.digest,
                    hashlib.sha256(files["software.tsv"]).hexdigest(),
                    hashlib.sha256(receipt).hexdigest(),
                )
            )
            + "\n"
        ).encode()
        proof_files = {"approval": marker, "approval.json": receipt}
        for name, raw in proof_files.items():
            (candidate.directory / name).write_bytes(raw)
        load_snapshot(candidate.directory)
    validate_run(api.json(f"{PREFIX}/actions/runs/{identity.run_id}"), identity)
    validate_candidate_ref(api, identity)
    if read_heads(api) != live_heads:
        raise CatalogError("source or base heads moved while collecting evidence")
    if read_protections(api, publisher_app_id, owner_type) != protections:
        raise CatalogError("branch protection changed while collecting evidence")
    plan = {
        "schema_version": 1,
        "status": "planned",
        "published": False,
        "repository": REPOSITORY,
        "repository_id": identity.repository_id,
        "parent": identity.candidate,
        "expected_heads": dict(expected_heads),
        "catalog_digest": candidate.digest,
        "evidence": evidence,
        "files": [
            {
                "path": "catalog/" + name,
                "mode": "100644",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "content_base64": base64.b64encode(raw).decode(),
            }
            for name, raw in sorted(proof_files.items())
        ],
        "pull_request": {"base": "development", "expected_base": development},
        "main_phase": "held-until-reviewed-development-merge",
        "limitations": [
            "read-only plan; App writer and candidate preparer are not implemented",
            "fresh live verification is required before any future write",
            "no atomic cross-repository source/base comparison",
        ],
    }
    if publisher_app_slug is not None:
        plan["publisher_app_slug"] = publisher_app_slug
        plan["publisher_app_id"] = publisher_app_id
    plan["transaction_id"] = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return plan


def plan_publication(
    api,
    identity,
    *,
    expected_heads,
    expected_sources,
    required_jobs,
    publisher_app_id,
    publisher_app_slug=None,
):
    return _plan_publication(
        api,
        identity,
        expected_heads=expected_heads,
        expected_sources=expected_sources,
        required_jobs=required_jobs,
        publisher_app_id=publisher_app_id,
        publisher_app_slug=publisher_app_slug,
    )


def publication_status(api, identity, **policy):
    try:
        return plan_publication(api, identity, **policy)
    except (CatalogError, GitHubError, OSError) as error:
        return {"schema_version": 1, "status": "held", "published": False, "reason": str(error)}
