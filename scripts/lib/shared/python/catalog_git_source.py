"""Verify declared Git source bytes without running working-tree filters."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

from software_catalog import CatalogError

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MODES = frozenset({"100644", "100755", "120000", "160000"})
_MAX_FILES = 20_000
_MAX_FILE = 16_777_216
_MAX_TOTAL = 134_217_728


def read_git(root: Path, *arguments: str, missing: bool = False) -> str:
    try:
        result = subprocess.run(
            [
                "/usr/bin/git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-c",
                "credential.helper=",
                "-C",
                str(root),
                *arguments,
            ],
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(Path.home()),
                "LANG": "C.UTF-8",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise CatalogError("cannot read the declared source Git objects") from error
    if missing and result.returncode == 1:
        return ""
    if result.returncode or len(result.stdout) > _MAX_FILE:
        raise CatalogError("cannot read the declared source Git objects")
    return result.stdout.rstrip("\n")


def _require(condition: object) -> None:
    if not condition:
        raise CatalogError("source checkout is not clean or differs from its declared Git objects")


def _path(name: str) -> None:
    relative = PurePosixPath(name)
    _require(
        bool(name)
        and not relative.is_absolute()
        and str(relative) == name
        and not any(part in {".", "..", ".git"} for part in relative.parts)
    )


def verify_checkout(root: Path, revision: str, *, allow_ignored: bool = True) -> None:
    """Require matching HEAD, index, tracked bytes and untracked-file policy."""
    _require(_SHA.fullmatch(revision))
    _require(root.is_dir() and not root.is_symlink() and root.resolve() == root)
    _require(root.stat().st_uid == os.geteuid())
    # Refuse partial metadata before any operation could request missing objects.
    _require(
        not read_git(
            root,
            "config",
            "--includes",
            "--null",
            "--get-regexp",
            r"^(extensions\.partialclone|remote\..*\.promisor)$",
            missing=True,
        )
    )
    _require(read_git(root, "rev-parse", "--show-toplevel") == str(root))
    _require(read_git(root, "rev-parse", "HEAD") == revision)
    expected: dict[str, tuple[str, str]] = {}
    for record in read_git(root, "ls-tree", "-r", "-z", revision).split("\0"):
        if not record:
            continue
        try:
            header, name = record.split("\t", 1)
            mode, kind, blob = header.split()
        except ValueError as error:
            raise CatalogError("invalid declared source tree") from error
        _path(name)
        _require(mode in _MODES and kind == ("commit" if mode == "160000" else "blob"))
        _require(_SHA.fullmatch(blob))
        _require(name not in expected and len(expected) < _MAX_FILES)
        expected[name] = (mode, blob)
    _require(expected)
    indexed: dict[str, tuple[str, str]] = {}
    for record in read_git(root, "ls-files", "--stage", "-z").split("\0"):
        if not record:
            continue
        try:
            header, name = record.split("\t", 1)
            mode, blob, stage = header.split()
        except ValueError as error:
            raise CatalogError("invalid declared source index") from error
        _require(stage == "0" and name not in indexed and len(indexed) < _MAX_FILES)
        indexed[name] = (mode, blob)
    _require(indexed == expected)
    _require(not read_git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    if not allow_ignored:
        _require(
            not read_git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
        )
    total = 0
    for name, (mode, blob) in expected.items():
        path = root / name
        try:
            if mode == "160000":
                _require(not path.is_symlink())
                if path.exists():
                    _require(path.is_dir() and path.resolve() == path)
                    _require(path.stat().st_uid == os.geteuid() and not any(path.iterdir()))
                continue
            metadata = path.lstat()
            _require(metadata.st_uid == os.geteuid())
            if path.is_symlink():
                _require(mode == "120000")
                content = os.fsencode(os.readlink(path))
                actual = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content)
            else:
                _require(path.is_file() and path.resolve() == path and mode != "120000")
                _require(bool(metadata.st_mode & 0o100) == (mode == "100755"))
                _require(metadata.st_size <= _MAX_FILE)
                total += metadata.st_size
                _require(total <= _MAX_TOTAL)
                actual = hashlib.sha1(b"blob " + str(metadata.st_size).encode() + b"\0")
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1_048_576), b""):
                        actual.update(block)
            _require(actual.hexdigest() == blob)
        except OSError as error:
            raise CatalogError("cannot verify the declared source bytes") from error
