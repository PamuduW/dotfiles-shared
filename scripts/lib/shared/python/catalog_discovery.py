"""Bounded public publisher metadata resolution; this module never approves a candidate."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from software_catalog import CatalogError

_HOSTS = frozenset({"api.github.com", "registry.npmjs.org", "pypi.org", "gitlab.com"})
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_PACKAGE = re.compile(r"(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")

_OWNER_PROVIDERS = frozenset(
    {
        "node-lts",
        "go-stable",
        "chrome-for-testing",
        "claude-native",
        "antigravity-manifest",
        "pypi-tool",
        "docker-apt",
        "portainer-lts",
    }
)


class DiscoveryError(CatalogError):
    """The entire candidate must be held for incomplete or changed upstream metadata."""


class _PublisherRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        parsed = urlsplit(newurl)
        if (
            request.get_header("Authorization")
            and parsed.netloc != urlsplit(request.full_url).netloc
        ):
            raise DiscoveryError("metadata credentials cannot follow a cross-publisher redirect")
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _HOSTS
            or parsed.username
            or parsed.password
        ):
            raise DiscoveryError("metadata redirected outside the publisher allowlist")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def fetch_metadata(url: str) -> Mapping[str, Any]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _HOSTS
        or parsed.username
        or parsed.password
    ):
        raise DiscoveryError("metadata endpoint is not an allowlisted public publisher")
    headers = {"User-Agent": "agentbot-software-catalog/1"}
    token = os.environ.get("CATALOG_GITHUB_TOKEN", "")
    if parsed.netloc == "api.github.com" and token:
        if not re.fullmatch(r"[A-Za-z0-9_]+", token):
            raise DiscoveryError("invalid catalog metadata credential format")
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _PublisherRedirect()
            ).open(request, timeout=30) as response:
                if urlsplit(response.url).hostname not in _HOSTS:
                    raise DiscoveryError("metadata redirected outside the publisher allowlist")
                raw = response.read(8_388_609)
                if len(raw) > 8_388_608:
                    raise DiscoveryError("publisher metadata exceeded its size limit")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise DiscoveryError("publisher metadata is not an object")
            return data
        except (urllib.error.URLError, TimeoutError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code in {401, 403, 404, 429}:
                raise DiscoveryError(
                    f"publisher metadata refused the request (HTTP {error.code}); "
                    "check access or rate limits before retrying"
                ) from error
            if attempt == 2:
                raise DiscoveryError(
                    "publisher metadata unavailable after bounded retries"
                ) from error
            time.sleep(attempt + 1)
    raise DiscoveryError("publisher metadata unavailable")


def _gitlab_identity(
    locator: str, discovery: Mapping[str, str], fetch
) -> tuple[str, Mapping[str, Any]]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", locator) or any(
        part in {".", ".."} for part in locator.split("/")
    ):
        raise DiscoveryError("invalid GitLab publisher project")
    project = quote(locator, safe="")
    channel = discovery.get("channel", "")
    if not channel:
        default_channel = fetch(f"https://gitlab.com/api/v4/projects/{project}").get(
            "default_branch"
        )
        if not isinstance(default_channel, str):
            raise DiscoveryError("GitLab project has no default branch")
        channel = default_channel
    if not channel:
        raise DiscoveryError("GitLab project has no default branch")
    data = fetch(
        f"https://gitlab.com/api/v4/projects/{project}/repository/commits/{quote(channel, safe='')}"
    )
    revision = data.get("id", "")
    if not isinstance(revision, str) or not _SHA.fullmatch(revision):
        raise DiscoveryError("GitLab source did not return a full revision")
    return revision, data


def discover_identity(
    discovery: Mapping[str, str], *, fetch: Callable[[str], Mapping[str, Any]] = fetch_metadata
) -> tuple[str, Mapping[str, Any]]:
    provider, locator = discovery.get("provider"), discovery.get("locator", "")
    if provider in {"github-release", "github-source"}:
        if not _REPOSITORY.fullmatch(locator) or any(
            part in {".", ".."} for part in locator.split("/")
        ):
            raise DiscoveryError("invalid GitHub publisher repository")
        if provider == "github-source":
            channel = quote(discovery.get("channel", "HEAD"), safe="")
            data = fetch(f"https://api.github.com/repos/{locator}/commits/{channel}")
            revision = data.get("sha", "")
            if not isinstance(revision, str) or not _SHA.fullmatch(revision):
                raise DiscoveryError("GitHub source did not return a full revision")
            return revision, data
        data = fetch(f"https://api.github.com/repos/{locator}/releases/latest")
        if data.get("draft") is not False or data.get("prerelease") is not False:
            raise DiscoveryError("publisher release is draft or prerelease")
        version = str(data.get("tag_name", "")).removeprefix(discovery.get("tag_prefix", "v"))
    elif provider == "gitlab-source":
        return _gitlab_identity(locator, discovery, fetch)
    elif provider == "npm":
        if not _PACKAGE.fullmatch(locator) or locator in {".", ".."}:
            raise DiscoveryError("invalid npm publisher package")
        channel = quote(discovery.get("channel", "latest"), safe="")
        data = fetch(f"https://registry.npmjs.org/{quote(locator, safe='')}/{channel}")
        version = str(data.get("version", ""))
    elif provider == "pypi":
        if not _PACKAGE.fullmatch(locator) or "/" in locator:
            raise DiscoveryError("invalid Python publisher package")
        data = fetch(f"https://pypi.org/pypi/{quote(locator, safe='')}/json")
        version = str(data.get("info", {}).get("version", ""))
    else:
        raise DiscoveryError(f"no supported discovery provider: {provider}")
    if not re.fullmatch(r"[0-9][A-Za-z0-9._+:-]*", version):
        raise DiscoveryError("publisher did not provide an exact release version")
    return version, data


def github_artifact(
    discovery: Mapping[str, str], release: Mapping[str, Any], version: str, target: str
) -> dict[str, str]:
    arch = target.rsplit("-", 1)[-1]
    substitutions = {
        "version": version,
        "arch": arch,
        "release_arch": "x86_64" if arch == "amd64" else "arm64",
        "triple": "x86_64" if arch == "amd64" else "aarch64",
        "arch_word": "64-bit" if arch == "amd64" else "arm64",
        "arch_bits": "64bit" if arch == "amd64" else "ARM64",
        "aarch64_suffix": "" if arch == "amd64" else "_aarch64",
    }
    try:
        name = discovery["asset"].format(**substitutions)
    except (KeyError, ValueError) as error:
        raise DiscoveryError("invalid release asset selection") from error
    assets = [asset for asset in release.get("assets", []) if asset.get("name") == name]
    if len(assets) != 1 or not _DIGEST.fullmatch(str(assets[0].get("digest", ""))):
        raise DiscoveryError(f"release asset missing or lacks publisher SHA256: {name}")
    url = assets[0].get("browser_download_url", "")
    expected = (
        f"https://github.com/{discovery['locator']}/releases/download/{release['tag_name']}/{name}"
    )
    if url != expected:
        raise DiscoveryError("release artifact URL differs from its declared publisher")
    artifact = {
        "version": version,
        "url": url,
        "digest": assets[0]["digest"],
    }
    for field in ("archive", "binary"):
        if field in discovery:
            try:
                artifact[field] = discovery[field].format(**substitutions)
            except (KeyError, ValueError) as error:
                raise DiscoveryError("invalid release extraction selection") from error
    return artifact


def reject_replaced_artifact(previous: Mapping[str, str], candidate: Mapping[str, str]) -> None:
    if (
        previous.get("version") == candidate.get("version")
        and "url" in previous
        and "url" in candidate
        and previous.get("digest") != candidate.get("digest")
    ):
        raise DiscoveryError(
            "publisher replaced bytes at an unchanged version; investigate before approval"
        )


def npm_artifact(discovery, metadata, version):
    package = discovery["locator"]
    distribution = metadata.get("dist", {})
    if not isinstance(distribution, Mapping):
        raise DiscoveryError("npm publisher distribution metadata is invalid")
    url = distribution.get("tarball", "")
    expected = f"https://registry.npmjs.org/{package}/-/{package.rsplit('/', 1)[-1]}-{version}.tgz"
    if url != expected:
        raise DiscoveryError("npm artifact URL differs from its declared publisher")
    integrity = distribution.get("integrity", "")
    try:
        if not isinstance(integrity, str) or not integrity.startswith("sha512-"):
            raise ValueError("SHA512 is required")
        digest = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
        if len(digest) != 64:
            raise ValueError("SHA512 must be 64 bytes")
    except (ValueError, binascii.Error) as error:
        raise DiscoveryError("npm publisher did not provide valid SHA512 integrity") from error
    artifact = {
        "version": version,
        "url": url,
        "digest": "sha512:" + digest.hex(),
        "package": package,
    }
    for field in ("archive", "binary"):
        if field in discovery:
            artifact[field] = discovery[field]
    return artifact


def _mutable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _mutable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable(item) for item in value]
    return value


def _npm_lock_artifact(key, discovery, version, tools, locks):
    from catalog_dependencies import generate_npm_lock, validate_npm_lock

    if set(tools) != {"npm", "node", "npm_version", "node_version"}:
        raise DiscoveryError("npm lock generation needs exact selected Node and npm tools")
    raw = generate_npm_lock(discovery["locator"], version, **tools)
    validate_npm_lock(raw, discovery["locator"], version)
    digest = hashlib.sha256(raw).hexdigest()
    path = f"locks/npm/{key}-{digest}.json"
    locks[path] = raw
    return {
        "version": version,
        "lock": path,
        "digest": "sha256:" + digest,
        "package": discovery["locator"],
        "archive": "npm-lock",
        "binary": discovery["binary"],
    }


def _source_targets(discovery, version, metadata, fetch):
    if discovery["provider"] == "github-release":
        revision, _ = discover_identity(
            {
                "provider": "github-source",
                "locator": discovery["locator"],
                "channel": metadata["tag_name"],
            },
            fetch=fetch,
        )
        return {"any": {"version": version, "revision": revision}}
    if discovery["provider"] not in {"github-source", "gitlab-source"}:
        raise DiscoveryError("source publisher must return an exact revision")
    return {"any": {"revision": version}}


def _require_discovery_providers(
    versioned, npm_tools, locks, extension_resolver, python_resolver, runtime_resolver
):
    supported = {
        "github-release",
        "github-source",
        "gitlab-source",
        "npm",
        "vscode-marketplace",
        "pypi",
    } | _OWNER_PROVIDERS
    problems = [
        f"{key}: " + ("; ".join(entry["blockers"]) or "publisher/lock provider is unavailable")
        for key, entry in sorted(versioned.items())
        if entry["blockers"]
        or (entry["discovery"].get("provider") in _OWNER_PROVIDERS and runtime_resolver is None)
        or entry["discovery"].get("provider") not in supported
        or (
            entry["discovery"].get("archive") == "npm-lock" and (npm_tools is None or locks is None)
        )
        or (
            entry["discovery"].get("provider") == "pypi"
            and (python_resolver is None or locks is None)
        )
        or (
            entry["kind"] == "extension"
            and (extension_resolver is None or not entry["discovery"].get("hosts"))
        )
    ]
    if problems:
        raise DiscoveryError("candidate held: " + " | ".join(problems))


def _component_host_targets(snapshot, components):
    from catalog_qualification import observation_targets

    targets = {key: set() for key in components}
    for target in snapshot.data["required_targets"]:
        for key, artifact_target in observation_targets(snapshot, target).values():
            targets[key].add(artifact_target)
    return targets


def build_candidate(
    snapshot,
    *,
    fetch=fetch_metadata,
    npm_tools=None,
    locks=None,
    extension_resolver=None,
    python_resolver=None,
    runtime_resolver=None,
) -> dict[str, Any]:
    """Resolve the complete declared inventory, or hold the entire proposal.

    This produces data only. It neither writes approval nor executes artifacts.
    Unsupported publishers and unresolved policy blockers fail before network IO.
    """
    from catalog_promotion import verify_candidate_policy
    from catalog_qualification import qualification_components
    from software_catalog import _UNVERSIONED, _validate

    candidate = _mutable(snapshot.data)
    selected = qualification_components(snapshot)
    versioned = {
        key: entry
        for key, entry in candidate["components"].items()
        if entry["kind"] not in _UNVERSIONED and key in selected
    }
    _require_discovery_providers(
        versioned, npm_tools, locks, extension_resolver, python_resolver, runtime_resolver
    )
    host_targets = _component_host_targets(snapshot, versioned)
    for key, entry in sorted(versioned.items()):
        try:
            discovery = entry["discovery"]
            if entry["kind"] == "extension":
                targets = host_targets[key]
                if not targets:
                    raise DiscoveryError(f"platform matrix has no declared extension host: {key}")
                entry["targets"] = {
                    **entry["targets"],
                    **_resolve_extension_targets(
                        snapshot, entry, targets, extension_resolver, locks
                    ),
                }
                continue
            owned = _resolve_owned_targets(
                snapshot,
                key,
                entry,
                candidate["required_targets"],
                python_resolver,
                runtime_resolver,
                locks,
            )
            if owned is not None:
                entry["targets"] = owned
                continue
            version, metadata = discover_identity(discovery, fetch=fetch)
            if entry["kind"] == "git-source":
                entry["targets"] = _source_targets(discovery, version, metadata, fetch)
                continue
            if discovery["provider"] not in {"github-release", "npm"}:
                raise DiscoveryError(f"binary publisher does not provide release artifacts: {key}")
            previous = entry["targets"]
            targets = set(candidate["required_targets"])
            if "any" in previous and discovery["provider"] != "npm":
                raise DiscoveryError(
                    f"architecture-specific release cannot use an any target: {key}"
                )
            artifacts = {}
            locked = None
            if discovery.get("archive") == "npm-lock":
                locked = _npm_lock_artifact(key, discovery, version, npm_tools, locks)
            for target in sorted(targets):
                if locked is not None:
                    artifact = dict(locked)
                elif discovery["provider"] == "npm":
                    artifact = npm_artifact(discovery, metadata, version)
                else:
                    artifact = github_artifact(discovery, metadata, version, target)
                    if artifact.get("archive") == "deb":
                        artifact = inspect_deb(artifact, key, target)
                prior = previous.get(target)
                if prior:
                    reject_replaced_artifact(prior, artifact)
                    _verify_previous_lock(snapshot, prior, artifact, locks)
                artifacts[target] = artifact
            entry["targets"] = {**previous, **artifacts}
        except (CatalogError, OSError) as error:
            raise DiscoveryError(f"{key}: {error}") from error
    _validate(candidate)
    verify_candidate_policy(_mutable(snapshot.data), candidate)
    return candidate


def write_candidate(snapshot, candidate, destination, locks):
    """Materialize unapproved data in a new private directory; never replace a catalog."""
    import os
    import shutil
    import tempfile
    from pathlib import Path

    from software_catalog import _read, _validate, load_snapshot, render_table

    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise DiscoveryError("candidate output already exists; preserving it")
    _validate(candidate)
    created = False
    try:
        with tempfile.TemporaryDirectory(
            prefix=".catalog-proposal-", dir=destination.parent
        ) as temporary:
            stage = Path(temporary)
            referenced = {
                artifact["lock"]
                for entry in candidate["components"].values()
                for artifact in entry["targets"].values()
                if "lock" in artifact
            }
            for name in referenced:
                raw = locks[name] if name in locks else _read(snapshot.directory / name, 16_777_216)
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            (stage / "software.json").write_text(
                json.dumps(candidate, indent=2, sort_keys=True) + "\n"
            )
            proposed = load_snapshot(stage, require_approved=False, check_table=False)
            (stage / "software.tsv").write_text(render_table(proposed))
            load_snapshot(stage, require_approved=False)
            destination.mkdir(mode=0o700)
            created = True
            for name in ("locks", "software.json", "software.tsv"):
                if (stage / name).exists():
                    os.replace(stage / name, destination / name)
            return load_snapshot(destination, require_approved=False)
    except (OSError, CatalogError):
        if created:
            shutil.rmtree(destination)
        raise


_ARTIFACT_HOSTS = frozenset(
    {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
)


class _ArtifactRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, publisher="github"):
        super().__init__()
        self.publisher = publisher

    def redirect_request(self, request, fp, code, message, headers, newurl):
        parsed = urlsplit(newurl)
        host = parsed.hostname or ""
        allowed = host in _ARTIFACT_HOSTS
        if self.publisher == "vscode-marketplace":
            allowed = host == "marketplace.visualstudio.com" or host.endswith(
                (".gallerycdn.vsassets.io", ".gallery.vsassets.io")
            )
        if parsed.scheme != "https" or not allowed or parsed.username or parsed.password:
            raise DiscoveryError("artifact redirected outside its public publisher hosts")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def public_response_stream(response):
    import gzip

    encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
    if encoding == "gzip":
        return gzip.GzipFile(fileobj=response)
    if encoding in {"", "identity"}:
        return response
    raise DiscoveryError("publisher response uses an unsupported content encoding")


def download_artifact(url, destination, *, publisher="github"):
    """Stream bounded public release bytes without executing them or inheriting auth."""
    parsed = urlsplit(url)
    valid_query = not parsed.query
    origin = "github.com"
    if publisher == "vscode-marketplace":
        origin = "marketplace.visualstudio.com"
        valid_query = not parsed.query or bool(
            re.fullmatch(r"targetPlatform=(?:linux|win32)-(?:x64|arm64)", parsed.query)
        )
    elif publisher != "github":
        raise DiscoveryError("unsupported artifact publisher")
    if (
        parsed.scheme != "https"
        or parsed.netloc != origin
        or parsed.username
        or parsed.password
        or not valid_query
        or parsed.fragment
    ):
        raise DiscoveryError("artifact URL differs from its public publisher policy")
    request = urllib.request.Request(url, headers={"User-Agent": "agentbot-software-catalog/1"})
    maximum = 268_435_456
    for attempt in range(3):
        created = False
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _ArtifactRedirect(publisher)
            )
            deadline = time.monotonic() + 120
            with (
                opener.open(request, timeout=30) as response,
                public_response_stream(response) as stream,
                destination.open("xb") as output,
            ):
                created = True
                count = 0
                while block := stream.read(1_048_576):
                    count += len(block)
                    if count > maximum or time.monotonic() > deadline:
                        raise DiscoveryError("publisher artifact exceeded its size or time limit")
                    output.write(block)
            return
        except (urllib.error.URLError, TimeoutError) as error:
            if created:
                destination.unlink()
            if attempt == 2:
                raise DiscoveryError(
                    "publisher artifact unavailable after bounded retries"
                ) from error
            time.sleep(attempt + 1)
        except Exception:
            if created:
                destination.unlink()
            raise


def inspect_deb(artifact, package, target, *, download=download_artifact):
    """Bind the package's own metadata; release tags are not Debian versions."""
    import subprocess
    import tempfile
    from pathlib import Path

    if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", package) or target.rsplit("-", 1)[-1] not in {
        "amd64",
        "arm64",
    }:
        raise DiscoveryError("invalid Debian package selection")
    with tempfile.TemporaryDirectory(prefix="catalog-deb-") as temporary:
        archive = Path(temporary) / "package.deb"
        download(artifact["url"], archive)
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            while block := stream.read(1_048_576):
                digest.update(block)
        if artifact["digest"] != "sha256:" + digest.hexdigest():
            raise DiscoveryError("Debian package differs from its publisher integrity")
        try:
            with (Path(temporary) / "metadata").open("w+b") as output:
                subprocess.run(
                    ["dpkg-deb", "--field", str(archive), "Package", "Version", "Architecture"],
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    timeout=30,
                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                )
                output.seek(0)
                raw = output.read(4097)
            if len(raw) > 4096:
                raise DiscoveryError("Debian metadata exceeded its size limit")
            fields = {}
            for line in raw.decode().splitlines():
                name, separator, value = line.partition(": ")
                if not separator or name in fields:
                    raise DiscoveryError("Debian metadata is malformed")
                fields[name] = value
            if (
                set(fields) != {"Package", "Version", "Architecture"}
                or fields["Package"] != package
                or fields["Architecture"] not in {"all", target.rsplit("-", 1)[-1]}
                or not re.fullmatch(r"[0-9][A-Za-z0-9.+:~_-]+", fields["Version"])
            ):
                raise DiscoveryError("Debian package identity, architecture or version differs")
            return {**artifact, "package": package, "version": fields["Version"]}
        except (OSError, UnicodeError, subprocess.SubprocessError) as error:
            raise DiscoveryError("cannot inspect Debian package metadata") from error


def reject_replaced_closure(previous: bytes, candidate: bytes, format_name: str) -> None:
    """A new dependency version may advance; unchanged versions cannot acquire new bytes."""
    from software_catalog import _object

    if format_name == "python-lock":
        _reject_replaced_python(previous, candidate)
        return
    tables = {"npm-lock": ("packages", "integrity"), "vsix-lock": ("extensions", "digest")}
    if format_name not in tables or any(len(raw) > 16_777_216 for raw in (previous, candidate)):
        raise DiscoveryError("cannot compare this dependency closure format or size")
    table, digest_field = tables[format_name]
    try:
        left, right = (json.loads(raw, object_pairs_hook=_object) for raw in (previous, candidate))
    except (ValueError, UnicodeError) as error:
        raise DiscoveryError("dependency closure is not unique-key JSON") from error
    if any(
        not isinstance(value, dict) or not isinstance(value.get(table), dict) or not value[table]
        for value in (left, right)
    ):
        raise DiscoveryError("dependency closure lacks its frozen package table")
    if format_name == "vsix-lock":
        _separate_builtin_requirements(left, right)
    for value in (left[table], right[table]):
        for name, entry in value.items():
            if format_name == "npm-lock" and name == "":
                continue
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("version"), str)
                or not isinstance(entry.get(digest_field), str)
            ):
                raise DiscoveryError("dependency closure lacks exact leaf versions and integrity")
    old = _closure_integrities(left[table], digest_field, format_name)
    new = _closure_integrities(right[table], digest_field, format_name)
    for identity in old.keys() & new.keys():
        if old[identity] != new[identity]:
            raise DiscoveryError("publisher replaced dependency bytes at an unchanged version")


def _closure_integrities(entries, digest_field, format_name):
    selected = {}
    for path, entry in entries.items():
        if format_name == "npm-lock" and path == "":
            continue
        identity = path
        if format_name == "npm-lock":
            identity = entry.get("name", path.rsplit("node_modules/", 1)[-1])
            if "resolved" in entry:
                parsed = urlsplit(entry["resolved"])
                if parsed.netloc != "registry.npmjs.org" or "/-/" not in parsed.path:
                    raise DiscoveryError("cannot identify public npm dependency bytes")
                identity = unquote(parsed.path.split("/-/", 1)[0].lstrip("/"))
            if not isinstance(identity, str) or not _PACKAGE.fullmatch(identity):
                raise DiscoveryError(
                    "cannot identify npm dependency independently of its placement"
                )
        selected.setdefault((identity, entry["version"]), set()).add(entry[digest_field])
    return selected


def _separate_builtin_requirements(left, right):
    builtins = []
    for lock in (left, right):
        selected = {}
        for name, entry in list(lock["extensions"].items()):
            if not isinstance(entry, dict) or "builtin" not in entry:
                continue
            if (
                lock.get("schema_version") != 2
                or not name.startswith("vscode.")
                or set(entry) != {"builtin", "requires"}
                or entry.get("builtin") is not True
                or not isinstance(entry.get("requires"), list)
                or any(
                    not isinstance(value, str) or not value.startswith("vscode.")
                    for value in entry["requires"]
                )
            ):
                raise DiscoveryError("invalid built-in editor requirement")
            selected[name] = lock["extensions"].pop(name)
        builtins.append(selected)
    for name in set(left["extensions"]) | set(right["extensions"]):
        if name in builtins[0] or name in builtins[1]:
            raise DiscoveryError("dependency changed between built-in and Marketplace ownership")


def _verify_previous_lock(snapshot, previous, artifact, locks):
    from software_catalog import _read

    if (
        not previous
        or "lock" not in previous
        or artifact.get("archive") not in {"npm-lock", "vsix-lock", "python-lock"}
    ):
        return
    if locks is None or artifact.get("lock") not in locks:
        raise DiscoveryError("candidate is missing its resolved dependency bytes")
    reject_replaced_closure(
        _read(snapshot.directory / previous["lock"], 16_777_216),
        locks[artifact["lock"]],
        artifact["archive"],
    )


def _resolve_extension_targets(snapshot, entry, targets, resolver, locks):
    artifacts = {}
    for target in sorted(targets):
        artifact = resolver(entry["discovery"]["locator"], target)
        _verify_previous_lock(snapshot, entry["targets"].get(target), artifact, locks)
        artifacts[target] = artifact
    return artifacts


def _reject_replaced_python(previous, candidate):
    from catalog_dependencies import validate_python_lock

    left, right = validate_python_lock(previous), validate_python_lock(candidate)
    for name in set(left) & set(right):
        if (
            left[name]["version"] == right[name]["version"]
            and left[name]["hashes"] != right[name]["hashes"]
        ):
            raise DiscoveryError("publisher replaced dependency bytes at an unchanged version")


def _resolve_python_targets(snapshot, entry, targets, resolver, locks):
    from catalog_dependencies import validate_python_lock

    artifacts = {}
    for target in sorted(set(targets)):
        artifact = resolver(entry["discovery"]["locator"], target)
        if artifact.get("archive") != "python-lock" or artifact.get("lock") not in locks:
            raise DiscoveryError("Python resolver did not provide its frozen dependency closure")
        raw = locks[artifact["lock"]]
        packages = validate_python_lock(raw)
        name = re.sub(r"[-_.]+", "-", entry["discovery"]["locator"]).lower()
        if (
            name not in packages
            or artifact.get("package") != name
            or artifact.get("version") != packages[name]["version"]
            or artifact.get("digest") != "sha256:" + hashlib.sha256(raw).hexdigest()
        ):
            raise DiscoveryError("Python candidate identity differs from its dependency closure")
        _verify_previous_lock(snapshot, entry["targets"].get(target), artifact, locks)
        artifacts[target] = artifact
    return {**entry["targets"], **artifacts}


def _resolve_owned_targets(snapshot, key, entry, targets, python_resolver, runtime_resolver, locks):
    provider = entry["discovery"]["provider"]
    if entry["kind"] == "python-package" and provider == "pypi":
        return _resolve_python_targets(snapshot, entry, targets, python_resolver, locks)
    if provider not in _OWNER_PROVIDERS:
        return None
    if entry["owner"] != "dotfiles" or entry["kind"] not in {"software", "vendor-apt"}:
        raise DiscoveryError("native runtime discovery requires its owning Dotfiles software entry")
    artifacts = {}
    for target in sorted(set(targets)):
        artifact = runtime_resolver(key, entry["discovery"], target)
        previous = entry["targets"].get(target)
        if previous:
            reject_replaced_artifact(previous, artifact)
        _verify_previous_lock(snapshot, previous, artifact, locks)
        artifacts[target] = artifact
    return {**entry["targets"], **artifacts}
