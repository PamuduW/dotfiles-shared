"""Explicit candidate qualification binds data and clean product source revisions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from catalog_git_source import read_git, verify_checkout
from software_catalog import CatalogError, CatalogSnapshot, load_snapshot

_SOURCES = frozenset({"agentbot", "dotfiles", "dotfiles-shared"})


def _verify_source(name: str, root: Path, expected: str) -> None:
    origin = read_git(root, "remote", "get-url", "origin").removesuffix(".git")
    if origin not in {
        f"https://github.com/PamuduW/{name}",
        f"git@github.com:PamuduW/{name}",
        f"ssh://git@github.com/PamuduW/{name}",
    }:
        raise CatalogError(f"qualification source origin differs from its authority: {name}")
    if read_git(root, "rev-parse", "HEAD") != expected:
        raise CatalogError(f"qualification source revision changed: {name}")
    verify_checkout(root, expected)


def qualification_snapshot(
    directory: Path, expected_digest: str, sources: Mapping[str, Path]
) -> CatalogSnapshot:
    """Validate explicit qualification input; never write or manufacture approval."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest) or set(sources) != _SOURCES:
        raise CatalogError("qualification needs an exact candidate digest and every frozen source")
    snapshot = load_snapshot(directory, require_approved=False)
    if snapshot.digest != expected_digest:
        raise CatalogError("qualification candidate digest changed")
    for name in sorted(_SOURCES):
        _verify_source(name, sources[name], snapshot.data["sources"][name])
    return snapshot


def load_qualification_context(path: Path):
    """Read an explicit, bounded manifest; environment variables never select one."""
    import hashlib
    import json

    from software_catalog import _object, _read, _text

    raw = _read(path, 1_048_576)
    try:
        data = json.loads(raw, object_pairs_hook=_object)
    except (ValueError, UnicodeError) as error:
        raise CatalogError("invalid qualification context JSON") from error
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "catalog", "digest", "sources"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or not isinstance(data["digest"], str)
        or not isinstance(data["sources"], dict)
        or set(data["sources"]) != _SOURCES
    ):
        raise CatalogError("qualification context schema mismatch")
    paths = [data["catalog"], *data["sources"].values()]
    if any(not _text(value) or not Path(value).is_absolute() for value in paths):
        raise CatalogError("qualification context requires absolute bounded paths")
    roots = {name: Path(root).resolve() for name, root in data["sources"].items()}
    snapshot = qualification_snapshot(Path(data["catalog"]), data["digest"], roots)
    return snapshot, roots, hashlib.sha256(raw).hexdigest()


if __name__ == "__main__":
    import hashlib
    import sys

    from software_catalog import _read

    try:
        if len(sys.argv) != 2:
            raise CatalogError("expected one explicit qualification context")
        operation, roots, context_digest = load_qualification_context(Path(sys.argv[1]))
        for item in (
            operation.directory,
            operation.digest,
            hashlib.sha256(_read(operation.directory / "software.tsv", 16_777_216)).hexdigest(),
            operation.data["sources"]["dotfiles-shared"],
            context_digest,
            roots["dotfiles-shared"],
            roots["dotfiles"],
            roots["agentbot"],
        ):
            print(item)
    except (CatalogError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from error
