"""Validate frozen public dependency closures before proposing or installing them."""

from __future__ import annotations

import base64
import binascii
import json
import re
from urllib.parse import urlsplit

from software_catalog import CatalogError, _object


def validate_npm_lock(raw: bytes, package: str, version: str) -> dict:
    if len(raw) > 16_777_216 or not re.fullmatch(
        r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*", package
    ):
        raise CatalogError("npm package identity or lock size is invalid")
    try:
        lock = json.loads(raw, object_pairs_hook=_object)
    except (ValueError, UnicodeError) as error:
        raise CatalogError("npm lock is not valid unique-key JSON") from error
    if (
        not isinstance(lock, dict)
        or type(lock.get("lockfileVersion")) is not int
        or lock.get("lockfileVersion") not in (2, 3)
    ):
        raise CatalogError("npm lock must contain a complete package closure")
    packages = lock.get("packages")
    if not isinstance(packages, dict) or not isinstance(packages.get(""), dict):
        raise CatalogError("npm lock lacks its root manifest")
    if packages[""].get("dependencies") != {package: version}:
        raise CatalogError("npm lock root differs from approved package/version")
    selected = packages.get("node_modules/" + package)
    if not isinstance(selected, dict) or selected.get("version") != version:
        raise CatalogError("npm lock is missing the exact approved package")
    for name, dependency in packages.items():
        if name == "":
            continue
        if (
            not name.startswith("node_modules/")
            or any(part in {".", "..", ""} for part in name.split("/"))
            or not isinstance(dependency, dict)
        ):
            raise CatalogError("npm lock contains an unsafe package path")
        parsed = urlsplit(str(dependency.get("resolved", "")))
        if (
            parsed.scheme != "https"
            or parsed.netloc != "registry.npmjs.org"
            or parsed.query
            or parsed.fragment
            or dependency.get("link")
            or not re.fullmatch(r"[0-9][A-Za-z0-9._+:-]*", str(dependency.get("version", "")))
        ):
            raise CatalogError("npm lock contains an unfrozen public registry dependency")
        integrity = dependency.get("integrity", "")
        try:
            if not isinstance(integrity, str) or not integrity.startswith("sha512-"):
                raise ValueError("SHA512 required")
            digest = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
            if len(digest) != 64:
                raise ValueError("SHA512 must be 64 bytes")
        except (ValueError, binascii.Error) as error:
            raise CatalogError("npm lock dependency lacks exact SHA512 integrity") from error
    return {
        "name": "agentbot-approved-cli",
        "version": "1.0.0",
        "private": True,
        "dependencies": {package: version},
    }


def generate_npm_lock(package, version, *, npm, node, npm_version, node_version) -> bytes:
    """Generate metadata only using explicitly selected tools and no inherited credentials."""
    import os
    import subprocess
    import tempfile
    from pathlib import Path

    from software_catalog import _read

    if not re.fullmatch(
        r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*", package
    ) or not re.fullmatch(r"[0-9][A-Za-z0-9._+:-]*", version):
        raise CatalogError("npm lock request requires an exact package and version")
    npm, node = Path(npm).resolve(), Path(node).resolve()
    if node.name != "node" or any(
        not path.is_file() or not os.access(path, os.X_OK) for path in (npm, node)
    ):
        raise CatalogError("dependency lock tools must be explicit executable paths")
    with tempfile.TemporaryDirectory(prefix="catalog-npm-lock-") as temporary:
        root = Path(temporary)
        (root / "home").mkdir()
        (root / "global.npmrc").touch()
        environment = {
            "PATH": f"{node.parent}:/usr/bin:/bin",
            "HOME": str(root / "home"),
            "TMPDIR": str(root),
            "LANG": "C.UTF-8",
            "NPM_CONFIG_USERCONFIG": "/dev/null",
            "NPM_CONFIG_GLOBALCONFIG": str(root / "global.npmrc"),
            "NPM_CONFIG_CACHE": str(root / "cache"),
            "NPM_CONFIG_IGNORE_SCRIPTS": "true",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_SSH_COMMAND": "false",
        }
        try:
            for executable, expected in ((node, "v" + node_version), (npm, npm_version)):
                with (root / "version.log").open("w+b") as log:
                    subprocess.run(
                        [str(executable), "--version"],
                        cwd=root,
                        env=environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        check=True,
                        timeout=30,
                    )
                    log.seek(0)
                    observed = log.read(257)
                if observed != (expected + "\n").encode():
                    raise CatalogError(
                        "dependency lock tool version differs from the selected catalog"
                    )
            manifest = {
                "name": "software-catalog-closure",
                "version": "1.0.0",
                "private": True,
                "dependencies": {package: version},
            }
            (root / "package.json").write_text(json.dumps(manifest) + "\n")
            with (root / "resolve.log").open("wb") as log:
                subprocess.run(
                    [
                        str(npm),
                        "install",
                        "--package-lock-only",
                        "--ignore-scripts",
                        "--no-audit",
                        "--no-fund",
                        "--include=optional",
                        "--registry=https://registry.npmjs.org",
                    ],
                    cwd=root,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=300,
                )
            raw = _read(root / "package-lock.json", 16_777_216)
            validate_npm_lock(raw, package, version)
            return raw
        except (OSError, subprocess.SubprocessError) as error:
            raise CatalogError(
                "dependency lock resolution failed or exceeded its timeout"
            ) from error


def validate_python_lock(raw: bytes) -> dict:
    """Accept only exact public-package pins and SHA256 wheel hashes."""
    if len(raw) > 16_777_216:
        raise CatalogError("Python dependency lock exceeds its size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise CatalogError("Python dependency lock must be UTF-8") from error
    if "\r" in text or "\x00" in text:
        raise CatalogError("Python dependency lock contains invalid control characters")
    text = text.replace("\\\n", " ")
    packages = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"([A-Za-z0-9][A-Za-z0-9._-]*)==([0-9][A-Za-z0-9._+!-]*)"
            r"((?:\s+--hash=sha256:[0-9a-f]{64})+)",
            line,
        )
        if match is None:
            raise CatalogError("Python lock requires exact pins and SHA256 hashes only")
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        if name in packages:
            raise CatalogError("Python lock contains duplicate normalized package names")
        packages[name] = {
            "version": match[2],
            "hashes": sorted(set(re.findall(r"sha256:[0-9a-f]{64}", match[3]))),
        }
    if not packages:
        raise CatalogError("Python dependency closure cannot be empty")
    return packages


def _python_requirements(requirements: str) -> str:
    if not isinstance(requirements, str) or len(requirements.encode()) > 1_048_576:
        raise CatalogError("Python requirements exceed their size limit")
    lines = []
    for line in requirements.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?"
            r"(?:\s*[<>=!~]+\s*[A-Za-z0-9.*+!-]+(?:\s*,\s*[<>=!~]+\s*[A-Za-z0-9.*+!-]+)*)?"
            r"(?:\s*;\s*[A-Za-z0-9_ .<>=!\"'()-]+)?",
            line,
        ):
            raise CatalogError("Python requirements must be public package constraints only")
        lines.append(line)
    if not lines:
        raise CatalogError("Python requirements cannot be empty")
    return "\n".join(lines) + "\n"


def generate_python_lock(
    requirements, *, uv, uv_version, python_version, target, constraints=None
) -> bytes:
    """Resolve wheel metadata in isolation; never build, install or execute packages."""
    import os
    import subprocess
    import tempfile
    from pathlib import Path

    from software_catalog import _read

    requirements = _python_requirements(requirements)
    if target != "linux-amd64" or not re.fullmatch(r"3\.12(?:\.[0-9]+)?", python_version):
        raise CatalogError("Python qualification currently requires Ubuntu AMD64 Python 3.12")
    uv = Path(uv).resolve()
    if not uv.is_file() or not os.access(uv, os.X_OK):
        raise CatalogError("Python lock tool must be an explicit executable path")
    with tempfile.TemporaryDirectory(prefix="catalog-python-lock-") as temporary:
        root = Path(temporary)
        (root / "home").mkdir()
        (root / "requirements.in").write_text(requirements)
        constraint_args = []
        if constraints is not None:
            selected = validate_python_lock(constraints)
            (root / "constraints.txt").write_text(
                "\n".join(
                    name + "==" + entry["version"] for name, entry in sorted(selected.items())
                )
                + "\n"
            )
            constraint_args = ["--constraint", "constraints.txt"]
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(root / "home"),
            "TMPDIR": str(root),
            "LANG": "C.UTF-8",
            "UV_CACHE_DIR": str(root / "cache"),
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_KEYRING_PROVIDER": "disabled",
        }
        try:
            with (root / "version.log").open("w+b") as log:
                subprocess.run(
                    [str(uv), "--version"],
                    cwd=root,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=30,
                )
                log.seek(0)
                if not re.fullmatch(
                    rb"uv " + re.escape(uv_version.encode()) + rb"(?: \([ -~]{1,160}\))?\n",
                    log.read(257),
                ):
                    raise CatalogError("Python dependency lock tool version differs from catalog")
            with (root / "resolve.log").open("wb") as log:
                subprocess.run(
                    [
                        str(uv),
                        "pip",
                        "compile",
                        "requirements.in",
                        *constraint_args,
                        "--upgrade",
                        "--generate-hashes",
                        "--only-binary=:all:",
                        "--no-config",
                        "--no-python-downloads",
                        "--no-header",
                        "--no-annotate",
                        "--no-emit-index-url",
                        "--python-version",
                        python_version,
                        "--python-platform",
                        "x86_64-unknown-linux-gnu",
                        "--index-url",
                        "https://pypi.org/simple",
                        "--output-file",
                        "requirements.lock",
                    ],
                    cwd=root,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=300,
                )
            packages = validate_python_lock(_read(root / "requirements.lock", 16_777_216))
            return (
                "\n".join(
                    name
                    + "=="
                    + entry["version"]
                    + " "
                    + " ".join("--hash=" + digest for digest in entry["hashes"])
                    for name, entry in sorted(packages.items())
                )
                + "\n"
            ).encode()
        except (OSError, subprocess.SubprocessError) as error:
            raise CatalogError("Python lock resolution failed or exceeded its timeout") from error
