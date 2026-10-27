"""Invoke the selected Python runtime owner through frozen public source code."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from catalog_context import load_qualification_context
from catalog_source_lease import lease_catalog_sources
from software_catalog import CatalogError, CatalogSnapshot


def selected_python_runtime(
    snapshot: CatalogSnapshot,
    authority: Path,
    *,
    qualification_context: Path | None = None,
) -> Path:
    component = snapshot.data["components"].get("python_runtime", {})
    if (
        component.get("owner") != "agentbot"
        or component.get("kind") != "software"
        or component.get("source") != "astral-sh/python-build-standalone"
    ):
        raise CatalogError("selected Python runtime lacks its declared owner")
    sources = None
    if qualification_context is not None:
        incoming, sources, _ = load_qualification_context(qualification_context)
        if (
            incoming.digest != snapshot.digest
            or incoming.directory != snapshot.directory
            or sources["dotfiles-shared"] != authority.resolve()
        ):
            raise CatalogError("Python owner and qualification context use different inputs")
    elif not snapshot.approved or not snapshot.revision:
        raise CatalogError("normal Python runtime acquisition requires an approved snapshot")
    with lease_catalog_sources(
        snapshot,
        authority,
        names=("agentbot", "dotfiles-shared"),
        qualification_sources=sources,
    ) as roots:
        helper = roots["agentbot"] / "scripts/catalog/python-runtime.py"
        if (
            helper.is_symlink()
            or not helper.is_file()
            or not helper.resolve().is_relative_to(roots["agentbot"])
        ):
            raise CatalogError("selected Python owner helper is unavailable")
        argv = ["/usr/bin/python3", "-I", "-B", str(helper), "--digest", snapshot.digest]
        if qualification_context is not None:
            argv.extend(("--qualification-context", str(qualification_context)))
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(Path.home()),
            "LANG": "C.UTF-8",
            "DOTFILES_SHARED_DIR": str(roots["dotfiles-shared"]),
            "SOFTWARE_CATALOG_REVISION": snapshot.revision
            or snapshot.data["sources"]["dotfiles-shared"],
        }
        for key in (
            "XDG_CACHE_HOME",
            "XDG_DATA_HOME",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "https_proxy",
            "http_proxy",
            "all_proxy",
            "no_proxy",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "CURL_CA_BUNDLE",
        ):
            if key in os.environ:
                environment[key] = os.environ[key]
        try:
            result = subprocess.run(
                argv, env=environment, text=True, capture_output=True, timeout=900, check=False
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CatalogError("selected Python owner helper could not finish") from error
        if result.returncode:
            diagnostic = (result.stderr + "\n" + result.stdout).strip()[-2048:]
            for value in sorted(set(environment.values()), key=len, reverse=True):
                if len(value) >= 4:
                    diagnostic = diagnostic.replace(value, "[redacted]")
            raise CatalogError("selected Python runtime acquisition failed: " + diagnostic)
        lines = result.stdout.strip().splitlines()
        fields = lines[0].split("\t") if len(lines) == 1 else []
        if len(fields) != 2 or re.fullmatch(r"[0-9a-f]{64}", fields[1]) is None:
            raise CatalogError("selected Python owner returned an invalid runtime identity")
        binary = Path(fields[0])
        if not binary.is_absolute() or not binary.is_file() or not os.access(binary, os.X_OK):
            raise CatalogError("selected Python owner did not provide its executable")
        return binary
