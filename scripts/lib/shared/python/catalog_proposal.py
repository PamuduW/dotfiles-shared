"""Prepare immutable catalog proposals with isolated, explicitly pinned tooling."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from catalog_context import _verify_source
from catalog_discovery import _mutable, build_candidate, fetch_metadata, write_candidate
from catalog_seed import build_bootstrap, verify_bootstrap_tools
from software_catalog import CatalogError, _git, _object, _read, load_snapshot, resolve_entry


def runtime_resolver(source, snapshot, uv, locks, *, require_approved=True):
    if source is None:
        return None
    _verify_source("dotfiles", source, snapshot.data["sources"]["dotfiles"])
    owner = source.resolve()
    path = owner / "scripts/catalog/runtime_discovery.py"
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(owner):
        raise CatalogError("native runtime discovery requires its frozen owning source")
    spec = importlib.util.spec_from_file_location("dotfiles_runtime_discovery", path)
    if spec is None or spec.loader is None:
        raise CatalogError("cannot load native runtime publisher resolver")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RuntimeCandidateResolver(
        uv=uv,
        uv_version=resolve_entry(snapshot, "uv", "linux-amd64", require_approved=require_approved)[
            "version"
        ]
        if uv is not None
        else None,
        locks=locks,
    ).resolve


def _tool_descriptor(path, home, digest):
    data = json.loads(_read(path, 1_048_576), object_pairs_hook=_object)
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "target", "catalog_digest", "node", "npm", "uv"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["target"] != "linux-amd64"
        or data["catalog_digest"] != digest
    ):
        raise CatalogError("bootstrap tool descriptor identity differs from its catalog")
    tools = {}
    for name in ("node", "npm", "uv"):
        if not isinstance(data[name], str) or not Path(data[name]).is_absolute():
            raise CatalogError("bootstrap tools need absolute executable paths")
        selected = Path(data[name]).resolve(strict=True)
        if (
            not selected.is_relative_to(home)
            or not selected.is_file()
            or not os.access(selected, os.X_OK)
        ):
            raise CatalogError("bootstrap tool escaped its private qualification home")
        tools[name] = selected
    return tools


def _install_tools(snapshot, roots, temporary):
    context = temporary / "bootstrap-context.json"
    context.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "catalog": str(snapshot.directory),
                "digest": snapshot.digest,
                "sources": {key: str(root) for key, root in roots.items()},
            }
        )
    )
    home, descriptor = temporary / "tool-home", temporary / "tools.json"
    try:
        with (temporary / "bootstrap.log").open("wb") as log:
            subprocess.run(
                [
                    "bash",
                    str(roots["dotfiles"] / "scripts/catalog/bootstrap-tools.sh"),
                    str(context),
                    str(home),
                    str(descriptor),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=900,
            )
    except (OSError, subprocess.SubprocessError) as error:
        diagnostic = _bootstrap_diagnostic(temporary / "bootstrap.log")
        raise CatalogError(
            "initial discovery tool installation failed; approved data was preserved"
            + ("\n" + diagnostic if diagnostic else "")
        ) from error
    return _tool_descriptor(descriptor, home, snapshot.digest)


def _bootstrap_diagnostic(path):
    # The bootstrap's env-i child has no inherited credentials or user configuration.
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 4096))
            value = stream.read(4096).decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "".join(
        character for character in value if character.isprintable() or character == "\n"
    ).strip()


def _agentbot_resolvers(snapshot, root, locks, tools, uv_version):
    _verify_source("agentbot", root, snapshot.data["sources"]["agentbot"])
    sys.path.insert(0, str(root))
    from src import catalog_extension_discovery, catalog_python_discovery

    if any(
        not Path(module.__file__).resolve().is_relative_to(root)
        for module in (catalog_extension_discovery, catalog_python_discovery)
    ):
        raise CatalogError("candidate resolver was imported from a different Agentbot source")
    return (
        catalog_extension_discovery.ExtensionCandidateResolver(locks).resolve,
        catalog_python_discovery.PythonCandidateResolver(
            root, locks, uv=tools["uv"], uv_version=uv_version
        ).resolve,
    )


def propose(catalog, roots, output, *, seed=False):
    """Generate data only; no branch, approval or stable catalog is changed."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise CatalogError("candidate output already exists; preserving it")
    baseline = load_snapshot(catalog, require_approved=False)
    if not baseline.approved and not seed:
        raise CatalogError("no approved catalog; explicitly prepare the initial seed first")
    if seed and (baseline.approved or baseline.data["auto_promote"]):
        raise CatalogError("initial seed requires unapproved data and disabled promotion")
    if set(roots) != set(baseline.data["sources"]):
        raise CatalogError("proposal needs all frozen source roots")
    roots = {key: Path(root).resolve() for key, root in roots.items()}
    sources = {key: _git(root, "rev-parse", "HEAD") for key, root in roots.items()}
    for key, root in roots.items():
        _verify_source(key, root, sources[key])
    data = _mutable(baseline.data)
    data["sources"] = sources
    with tempfile.TemporaryDirectory(prefix="catalog-proposal-") as directory:
        temporary = Path(directory)
        basis = write_candidate(baseline, data, temporary / "basis", {})
        if seed:
            bootstrap = build_bootstrap(
                basis,
                fetch=fetch_metadata,
                runtime_resolver=runtime_resolver(roots["dotfiles"], basis, None, {}),
            )
            basis = write_candidate(basis, bootstrap, temporary / "bootstrap", {})
        tools = _install_tools(basis, roots, temporary)
        selected = {
            name: resolve_entry(basis, name, "linux-amd64", require_approved=False)["version"]
            for name in ("nodejs", "npm", "uv")
        }
        locks = {}
        extension, python = _agentbot_resolvers(
            basis, roots["agentbot"], locks, tools, selected["uv"]
        )
        candidate = build_candidate(
            basis,
            locks=locks,
            npm_tools={
                "node": tools["node"],
                "npm": tools["npm"],
                "node_version": selected["nodejs"],
                "npm_version": selected["npm"],
            },
            extension_resolver=extension,
            python_resolver=python,
            runtime_resolver=runtime_resolver(
                roots["dotfiles"], basis, tools["uv"], locks, require_approved=False
            ),
        )
        if seed:
            verify_bootstrap_tools(basis.data, candidate)
        candidate["generation"] = (
            "catalog-"
            + hashlib.sha256(
                json.dumps(
                    {**candidate, "generation": ""}, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()[:20]
        )
        for key, root in roots.items():
            _verify_source(key, root, sources[key])
        result = write_candidate(basis, candidate, output, locks)
    return result, result.digest != baseline.digest
