"""Strict software snapshots; parsing never downloads or approves software."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

_TOP = {"schema_version", "generation", "sources", "required_targets", "auto_promote", "components"}
_ENTRY = {"owner", "kind", "source", "discovery", "requires", "blockers", "targets"}
_KINDS = {
    "software",
    "external",
    "vendor-apt",
    "python-package",
    "git-source",
    "extension",
    "configuration",
    "distro",
    "group",
    "service",
    "integration",
}
_UNVERSIONED = {"configuration", "distro", "group", "service", "integration", "external"}
_FIELDS = {"version", "url", "digest", "archive", "binary", "package", "revision", "lock"}
_DISCOVERY = {
    "provider",
    "locator",
    "asset",
    "archive",
    "binary",
    "version_arg",
    "channel",
    "constraint",
    "auto_update_env",
    "auto_update_value",
    "hosts",
    "tag_prefix",
}
_ID = re.compile(r"[a-z][a-z0-9_.-]*\Z")
_TARGET = re.compile(r"(?:linux|windows|wsl2)-(?:amd64|arm64)\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")


class CatalogError(ValueError):
    """The snapshot is incomplete, unapproved or unsafe to consume."""


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError(f"duplicate catalog key: {key}")
        result[key] = value
    return result


def _text(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and all(ord(c) >= 32 and ord(c) != 127 for c in value)
    )


def _keys(value: Any, allowed: set[str], *, complete: bool = True) -> None:
    if not isinstance(value, dict) or set(value) - allowed or (complete and set(value) != allowed):
        raise CatalogError("catalog fields do not match schema 1")


def _artifact(value: Any, kind: str) -> None:
    _keys(value, _FIELDS, complete=False)
    if not value or not all(_text(v) for v in value.values()):
        raise CatalogError("artifact fields must be nonempty text without control characters")
    if "revision" in value and not _REVISION.fullmatch(value["revision"]):
        raise CatalogError("source revision must be a full Git SHA")
    if kind == "git-source":
        if "revision" not in value:
            raise CatalogError("Git source artifact requires an exact revision")
        return
    version_pattern = (
        r"[0-9][A-Za-z0-9.+:~_-]*"
        if kind == "vendor-apt" or value.get("archive") == "deb"
        else r"[A-Za-z0-9][A-Za-z0-9._+:-]*"
    )
    if "version" not in value or not re.fullmatch(version_pattern, value["version"]):
        raise CatalogError("artifact requires an exact version")
    digest = value.get("digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}|sha512:[0-9a-f]{128}", digest):
        raise CatalogError("artifact requires SHA-256/SHA-512 integrity")
    if "url" in value:
        parsed = urlsplit(value["url"])
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise CatalogError("artifact URL must be stable HTTPS without credentials")
    elif "lock" not in value:
        raise CatalogError("artifact needs a download URL or frozen dependency lock")
    if "lock" in value:
        path = Path(value["lock"])
        if (
            path.is_absolute()
            or ".." in path.parts
            or not re.fullmatch(r"locks/[A-Za-z0-9_./-]+", value["lock"])
            or path.as_posix() != value["lock"]
            or not digest.startswith("sha256:")
        ):
            raise CatalogError("dependency lock must stay inside the catalog snapshot")


def _validate_entry(key: str, entry: Any) -> None:
    if not _ID.fullmatch(key):
        raise CatalogError("invalid software identity")
    _keys(entry, _ENTRY)
    if (
        entry["owner"] not in {"dotfiles", "agentbot", "dotfiles-shared"}
        or entry["kind"] not in _KINDS
        or not _text(entry["source"])
    ):
        raise CatalogError(f"invalid ownership or classification for {key}")
    _keys(entry["discovery"], _DISCOVERY, complete=False)
    if not all(_text(v) for v in entry["discovery"].values()):
        raise CatalogError("discovery metadata must be bounded text")
    if "hosts" in entry["discovery"]:
        hosts = entry["discovery"]["hosts"].split(",")
        if (
            entry["kind"] != "extension"
            or not hosts
            or hosts != sorted(set(hosts))
            or set(hosts) - {"windows", "wsl"}
        ):
            raise CatalogError("extension hosts must explicitly name windows and/or wsl")
    for field in ("requires", "blockers"):
        if not isinstance(entry[field], list) or not all(_text(v) for v in entry[field]):
            raise CatalogError(f"invalid {field} for {key}")
    if not isinstance(entry["targets"], dict) or (
        entry["kind"] in _UNVERSIONED and entry["targets"]
    ):
        raise CatalogError("unversioned components cannot carry artifact versions")
    for target, artifact in entry["targets"].items():
        if target != "any" and not _TARGET.fullmatch(target):
            raise CatalogError("invalid artifact target")
        _artifact(artifact, entry["kind"])


def _validate(data: Any) -> None:
    _keys(data, _TOP)
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise CatalogError("unsupported software catalog schema")
    if not _text(data["generation"]) or not re.fullmatch(r"[A-Za-z0-9._-]+", data["generation"]):
        raise CatalogError("invalid catalog generation")
    if type(data["auto_promote"]) is not bool:
        raise CatalogError("auto_promote must be a boolean")
    _keys(data["sources"], {"dotfiles", "agentbot", "dotfiles-shared"})
    if not all(isinstance(v, str) and _REVISION.fullmatch(v) for v in data["sources"].values()):
        raise CatalogError("catalog sources require full Git revisions")
    targets = data["required_targets"]
    if (
        not isinstance(targets, list)
        or not targets
        or any(not isinstance(t, str) or not _TARGET.fullmatch(t) for t in targets)
        or len(set(targets)) != len(targets)
    ):
        raise CatalogError("required target matrix is invalid or empty")
    components = data["components"]
    if not isinstance(components, dict) or not components:
        raise CatalogError("component inventory cannot be empty")
    for key, entry in components.items():
        _validate_entry(key, entry)
    for entry in components.values():
        if any(dependency not in components for dependency in entry["requires"]):
            raise CatalogError("catalog has an unknown dependency")
    dependency_order(components)


def dependency_order(components: Mapping[str, Any]) -> tuple[str, ...]:
    """Return a deterministic dependency-first order without recursive traversal."""
    pending = {}
    dependents = defaultdict(list)
    for key in sorted(components):
        required = components[key]["requires"]
        if len(set(required)) != len(required) or any(name not in components for name in required):
            raise CatalogError("catalog has duplicate or unknown dependency edges")
        pending[key] = len(required)
        for name in required:
            dependents[name].append(key)
    ready = deque(key for key in sorted(pending) if pending[key] == 0)
    ordered = []
    while ready:
        key = ready.popleft()
        ordered.append(key)
        for name in dependents[key]:
            pending[name] -= 1
            if pending[name] == 0:
                ready.append(name)
    if len(ordered) != len(components):
        raise CatalogError("catalog dependency graph contains a cycle")
    return tuple(ordered)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class CatalogSnapshot:
    directory: Path
    digest: str
    data: Mapping[str, Any]
    approved: bool
    revision: str | None = None


def _read(path: Path, maximum: int) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise CatalogError("catalog inputs must be regular files, not symlinks")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise CatalogError("catalog input exceeds its size limit")
    return raw


def render_table(snapshot: CatalogSnapshot) -> str:
    rows = [f"#software-catalog-v1\t{snapshot.digest}"]
    for key, entry in sorted(snapshot.data["components"].items()):
        for field in ("owner", "kind", "source"):
            rows.append(f"{key}\t{field}\tany\t{entry[field]}")
        if entry["blockers"]:
            rows.append(f"{key}\tblockers\tany\t{'; '.join(entry['blockers'])}")
        for field, value in sorted(entry["discovery"].items()):
            rows.append(f"{key}\tdiscovery.{field}\tany\t{value}")
        for target, artifact in sorted(entry["targets"].items()):
            for field, value in sorted(artifact.items()):
                rows.append(f"{key}\t{field}\t{target}\t{value}")
    return "\n".join(rows) + "\n"


def _locks(data: Mapping[str, Any]) -> dict[str, str]:
    locks: dict[str, str] = {}
    for entry in data["components"].values():
        for artifact in entry["targets"].values():
            if "lock" in artifact:
                name, digest = artifact["lock"], artifact["digest"]
                if name in locks and locks[name] != digest:
                    raise CatalogError("dependency lock has conflicting digests")
                locks[name] = digest
    return locks


def _verify_locks(directory: Path, data: Mapping[str, Any]) -> None:
    for name, digest in _locks(data).items():
        path = directory / name
        if any(parent.is_symlink() for parent in path.parents):
            raise CatalogError("dependency lock cannot cross a symlink")
        if "sha256:" + _digest(_read(path, 16_777_216)) != digest:
            raise CatalogError("dependency lock integrity mismatch")


def load_snapshot(
    directory: Path, *, require_approved: bool = True, check_table: bool = True
) -> CatalogSnapshot:
    try:
        raw = _read(directory / "software.json", 4_194_304)
        data = json.loads(raw, object_pairs_hook=_object)
        _validate(data)
        _verify_locks(directory, data)
        candidate = CatalogSnapshot(directory.resolve(), _digest(raw), _freeze(data), False)
        table = render_table(candidate).encode()
        if check_table and _read(directory / "software.tsv", 16_777_216) != table:
            raise CatalogError("generated catalog table is stale or modified")
        approved = False
        proof = directory / "approval"
        if proof.exists():
            lines = _read(proof, 4096).decode().splitlines()
            receipt = _read(directory / "approval.json", 1_048_576)
            approved = lines == [
                "software-approved-v1",
                _digest(raw),
                _digest(table),
                _digest(receipt),
            ]
            if not approved:
                raise CatalogError("catalog approval does not bind the snapshot bytes")
        if require_approved and not approved:
            raise CatalogError("software catalog has no qualification approval")
        return CatalogSnapshot(candidate.directory, candidate.digest, candidate.data, approved)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as error:
        raise CatalogError(f"cannot read software catalog: {type(error).__name__}") from error


def resolve_entry(
    snapshot: CatalogSnapshot, component_id: str, target: str, *, require_approved: bool = True
) -> Mapping[str, str]:
    if require_approved and not snapshot.approved:
        raise CatalogError("software catalog has no qualification approval")
    entry = snapshot.data["components"].get(component_id)
    if entry is None:
        raise CatalogError(f"unknown catalog component: {component_id}")
    if entry["blockers"]:
        raise CatalogError(f"{component_id}: {'; '.join(entry['blockers'])}")
    artifact = entry["targets"].get(target)
    if artifact is None and target in {"wsl2-amd64", "wsl2-arm64"}:
        artifact = entry["targets"].get("linux-" + target.removeprefix("wsl2-"))
    if artifact is None:
        artifact = entry["targets"].get("any")
    if artifact is None:
        raise CatalogError(f"no catalog target for {component_id}/{target}")
    return artifact


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        timeout=30,
    )
    if result.returncode:
        raise CatalogError("cannot resolve the approved catalog Git revision")
    return result.stdout.strip()


def _verify_cached(destination: Path, staging: Path, names: list[str]) -> None:
    if destination.is_symlink() or not destination.is_dir():
        raise CatalogError("unsafe catalog cache destination")
    for name in names:
        if _read(destination / name, 16_777_216) != (staging / name).read_bytes():
            raise CatalogError("cached snapshot differs from its approved Git objects")


def freeze_approved_snapshot(
    shared_root: Path, cache_root: Path, *, revision: str | None = None
) -> CatalogSnapshot:
    origin = _git(shared_root, "remote", "get-url", "origin").removesuffix(".git")
    if origin not in {
        "git@github.com:PamuduW/dotfiles-shared",
        "https://github.com/PamuduW/dotfiles-shared",
        "ssh://git@github.com/PamuduW/dotfiles-shared",
    }:
        raise CatalogError("catalog origin is not the configured public catalog authority")
    selected = revision or _git(shared_root, "rev-parse", "refs/remotes/origin/main")
    if not _REVISION.fullmatch(selected):
        raise CatalogError("catalog snapshot revision must be a full Git SHA")
    _git(shared_root, "merge-base", "--is-ancestor", selected, "refs/remotes/origin/main")
    if cache_root.is_symlink() or any(parent.is_symlink() for parent in cache_root.parents):
        raise CatalogError("catalog cache cannot cross a symlink")
    cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if cache_root.stat().st_uid != os.geteuid() or cache_root.stat().st_mode & 0o077:
        raise CatalogError("catalog cache must be private and owned by this user")
    destination = cache_root / selected
    staging = Path(tempfile.mkdtemp(prefix=".catalog-", dir=cache_root))
    names = ["software.json", "software.tsv", "approval", "approval.json"]
    try:
        for name in names:
            result = subprocess.run(
                ["git", "-C", str(shared_root), "show", f"{selected}:catalog/{name}"],
                capture_output=True,
                timeout=30,
            )
            if result.returncode:
                raise CatalogError("approved main does not contain a qualified catalog")
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(result.stdout)
            if name == "software.json":
                data = json.loads(result.stdout, object_pairs_hook=_object)
                _validate(data)
                names.extend(sorted(_locks(data)))
        load_snapshot(staging)
        if destination.exists() or destination.is_symlink():
            _verify_cached(destination, staging, names)
        else:
            try:
                staging.rename(destination)
            except OSError as error:
                if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
                _verify_cached(destination, staging, names)
        snapshot = load_snapshot(destination)
        return CatalogSnapshot(snapshot.directory, snapshot.digest, snapshot.data, True, selected)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
