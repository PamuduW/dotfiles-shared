"""Lease exact public catalog sources without modifying installed checkouts."""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from catalog_context import _SOURCES
from software_catalog import CatalogError, CatalogSnapshot


def private_directory(path: Path) -> None:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise CatalogError("private catalog cache cannot cross a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077:
        raise CatalogError("catalog dependency cache must be private and owned")


@contextmanager
def _lease_lock(path: Path):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise CatalogError("unsafe catalog dependency lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _git(root: Path, runner: Any, *arguments: str) -> str:
    argv = [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "credential.helper=",
        "-C",
        str(root),
        *arguments,
    ]
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(Path.home()),
        "LANG": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    if runner is None:
        try:
            outcome = subprocess.run(
                argv, timeout=180, env=environment, capture_output=True, text=True, check=True
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CatalogError(
                f"cannot lease the qualified catalog source: Git {arguments[0]} failed"
            ) from error
        return outcome.stdout.strip()
    result = runner.run(argv, timeout_seconds=180, env=environment)
    if not result.ok:
        raise CatalogError("cannot lease the qualified catalog source: " + result.detail())
    return result.stdout.strip()


def _verify_source(name: str, root: Path, revision: str, runner: Any) -> None:
    if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
        raise CatalogError("qualified source cannot cross a symlink")
    origin = _git(root, runner, "remote", "get-url", "origin").removesuffix(".git")
    if origin not in {
        f"https://github.com/PamuduW/{name}",
        f"git@github.com:PamuduW/{name}",
        f"ssh://git@github.com/PamuduW/{name}",
    }:
        raise CatalogError("qualified source origin differs from its public authority")
    if _git(root, runner, "rev-parse", "HEAD") != revision:
        raise CatalogError("qualified source revision changed")
    if _git(root, runner, "status", "--porcelain=v1", "--untracked-files=all"):
        raise CatalogError("qualified source is not clean")


@contextmanager
def lease_catalog_sources(
    snapshot: CatalogSnapshot,
    authority: Path,
    runner: Any = None,
    *,
    names: tuple[str, ...] = ("dotfiles", "dotfiles-shared"),
    qualification_sources: Mapping[str, Path] | None = None,
) -> Iterator[dict[str, Path]]:
    """Lease exact public source SHAs without modifying an operator checkout."""
    expected = snapshot.data["sources"]
    if not names or len(set(names)) != len(names) or not set(names) <= _SOURCES:
        raise CatalogError("source lease needs unique declared public source identities")
    for name in names:
        if re.fullmatch(r"[0-9a-f]{40}", expected[name]) is None:
            raise CatalogError("qualified source needs an exact Git revision")
    if qualification_sources is not None:
        qualified_roots = {name: qualification_sources[name].resolve() for name in names}
        for name, root in qualified_roots.items():
            _verify_source(name, root, expected[name], runner)
        yield qualified_roots
        for name, root in qualified_roots.items():
            _verify_source(name, root, expected[name], runner)
        return
    if (
        not snapshot.approved
        or not snapshot.revision
        or not re.fullmatch(r"[0-9a-f]{40}", snapshot.revision)
    ):
        raise CatalogError("normal runtime provisioning requires an approved catalog revision")
    origin = _git(authority, runner, "remote", "get-url", "origin").removesuffix(".git")
    if origin not in {
        "https://github.com/PamuduW/dotfiles-shared",
        "git@github.com:PamuduW/dotfiles-shared",
        "ssh://git@github.com/PamuduW/dotfiles-shared",
    }:
        raise CatalogError("catalog source authority has an untrusted origin")
    main = _git(authority, runner, "rev-parse", "refs/remotes/origin/main")
    if re.fullmatch(r"[0-9a-f]{40}", main) is None:
        raise CatalogError("catalog authority lacks an exact approved main revision")
    _git(authority, runner, "merge-base", "--is-ancestor", snapshot.revision, main)
    roots: dict[str, Path] = {}
    with ExitStack() as leases:
        for name in names:
            candidate = authority if name == "dotfiles-shared" else authority.parent / name
            try:
                _verify_source(name, candidate, expected[name], runner)
            except CatalogError:
                cache = snapshot.directory.parent / "sources"
                private_directory(cache)
                leases.enter_context(_lease_lock(cache / f"{name}-{expected[name]}.lock"))
                candidate = cache / f"{name}-{expected[name]}"
                if not candidate.exists() and not candidate.is_symlink():
                    staging = Path(tempfile.mkdtemp(prefix=".source-", dir=cache))
                    try:
                        _git(staging, runner, "init", "--quiet")
                        _git(
                            staging,
                            runner,
                            "remote",
                            "add",
                            "origin",
                            f"https://github.com/PamuduW/{name}.git",
                        )
                        _git(staging, runner, "fetch", "--no-tags", "origin", expected[name])
                        _git(staging, runner, "checkout", "--quiet", "--detach", expected[name])
                        _verify_source(name, staging, expected[name], runner)
                        staging.rename(candidate)
                    finally:
                        if staging.exists():
                            shutil.rmtree(staging)
                _verify_source(name, candidate, expected[name], runner)
                if name == "dotfiles-shared":
                    # Preserve the verified authority's exact ref, not a floating lookup.
                    _git(candidate, runner, "fetch", "--no-tags", "origin", main)
                    _git(candidate, runner, "update-ref", "refs/remotes/origin/main", main)
                    _git(candidate, runner, "merge-base", "--is-ancestor", snapshot.revision, main)
            roots[name] = candidate
        yield roots
        for name, root in roots.items():
            _verify_source(name, root, expected[name], runner)
