"""Audit fresh/repeat inventory evidence without granting qualification approval."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from catalog_dependencies import validate_python_lock
from catalog_qualification import observation_targets
from software_catalog import CatalogError, _object, _read, resolve_entry

SCOPES = frozenset(
    {
        "user-tools-smoke-only",
        "tooling-smoke-only",
        "vendor-package-smoke-only",
        "linux-font-smoke-only",
        "behavior-smoke-only",
        "integration-smoke-only",
        "docker-lifecycle-smoke-only",
    }
)
_BEHAVIOR = frozenset(
    {"node-execution", "nvm-execution", "go-compilation", "chrome-dom", "mermaid-svg"}
)


def _require(condition, message):
    if not condition:
        raise CatalogError(message)


def _mapping(value):
    _require(isinstance(value, dict), "smoke evidence section must be an object")
    return value


def _successes(receipt, fields):
    receipt = _mapping(receipt)
    _require(
        all(receipt.get(key) == "success" for key in fields), "incomplete smoke scenarios/checks"
    )


def _phase_versions(receipt, expected, *, vendor=False, digest=""):
    observations = receipt.get("observations")
    _require(
        isinstance(observations, dict) and set(observations) == {"fresh", "repeat"},
        "missing fresh/repeat observations",
    )
    for phase in observations.values():
        _require(
            isinstance(phase, dict) and set(phase) == set(expected),
            "smoke observations do not cover the selected inventory",
        )
        for key, artifact in expected.items():
            actual = phase[key]
            if vendor:
                _require(
                    isinstance(actual, dict)
                    and actual
                    == {
                        "package": artifact["package"],
                        "version": artifact["version"],
                        "receipt_digest": digest,
                        "hold": True,
                    },
                    "vendor version, ownership or hold differs from the candidate",
                )
            else:
                _require(
                    actual == artifact.get("revision", artifact.get("version")),
                    "installed version/revision differs from the candidate",
                )
    _require(
        observations["fresh"] == observations["repeat"], "repeat smoke changed installed evidence"
    )


def _extension_evidence(snapshot, selected, receipt):
    expected = {}
    for artifact in selected.values():
        lock = json.loads(
            _read(snapshot.directory / artifact["lock"], 16_777_216), object_pairs_hook=_object
        )
        _require(
            isinstance(lock, dict) and isinstance(lock.get("extensions"), dict),
            "invalid frozen extension closure",
        )
        for identity, entry in lock["extensions"].items():
            _mapping(entry)
            if entry.get("builtin") is True:
                continue
            proof = {"version": entry["version"], "digest": entry["digest"]}
            _require(
                identity not in expected or expected[identity] == proof,
                "extension closures disagree",
            )
            expected[identity] = proof
    actual = _mapping(receipt.get("extensions", {}))
    _require(
        actual.get("ownership") == expected
        and actual.get("versions") == {key: value["version"] for key, value in expected.items()},
        "extension closure observations differ from frozen versions/ownership",
    )
    _require(
        actual.get("settings_verified") is True
        and actual.get("repeat_no_downloads_or_installs") is True
        and actual.get("wsl_certified") is False,
        "incomplete Linux extension settings/repeat evidence",
    )


def _skill_evidence(selected, receipt):
    skills = _mapping(receipt.get("skills", {}))
    sources, entries = skills.get("sources", {}), skills.get("skills", {})
    _require(
        isinstance(sources, dict) and isinstance(entries, dict) and set(sources) == set(selected),
        "skill-source coverage differs from the inventory",
    )
    installed = set()
    for identity, (component, artifact) in selected.items():
        discovery = component["discovery"]
        provider = discovery.get("provider")
        _require(provider in {"github-source", "gitlab-source"}, "unsupported skill publisher")
        host, source_type = (
            ("gitlab.com", "gitlab") if provider == "gitlab-source" else ("github.com", "github")
        )
        locator = discovery.get("locator", "")
        _require(
            isinstance(locator, str)
            and re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", locator) is not None
            and all(part not in {".", ".."} for part in locator.split("/"))
            and component["source"]
            in (
                {locator, host + "/" + locator} if host == "github.com" else {host + "/" + locator}
            ),
            "skill source differs from its declared publisher",
        )
        source = sources[identity]
        _require(
            isinstance(source, dict)
            and source.get("repo") == component["source"]
            and source.get("revision") == artifact["revision"]
            and isinstance(source.get("installed"), list)
            and bool(source["installed"]),
            "skill-source provenance differs from the candidate",
        )
        for name in source["installed"]:
            _require(
                isinstance(name, str) and name not in installed and name in entries,
                "duplicate or missing installed skill",
            )
            installed.add(name)
            proof = entries[name]
            _require(
                isinstance(proof, dict)
                and proof.get("source") == component["source"]
                and proof.get("sourceRevision") == artifact["revision"]
                and proof.get("sourceType") == source_type
                and proof.get("sourceUrl") == f"https://{host}/{locator}.git"
                and isinstance(proof.get("skillFolderHash"), str)
                and re.fullmatch(r"[0-9a-f]{64}", proof["skillFolderHash"]) is not None,
                "skill content/provenance evidence differs from its frozen source",
            )
    _require(
        installed == set(entries) and skills.get("repeat_content_and_provenance") is True,
        "incomplete skill content/repeat evidence",
    )


def _partition(snapshot):
    expected = observation_targets(snapshot, "linux-amd64")
    _require(bool(expected), "empty inventory cannot pass smoke coverage")
    groups = {
        key: {}
        for key in (
            "user",
            "vendor",
            "python",
            "extension",
            "skill",
            "font",
            "oci",
            "tooling",
            "sdk",
        )
    }
    observations = {}
    for observation, (key, target) in expected.items():
        entry = snapshot.data["components"][key]
        artifact = resolve_entry(snapshot, key, target, require_approved=False)
        if entry["kind"] == "extension":
            groups["extension"][key] = artifact
        elif entry["owner"] == "agentbot" and entry["kind"] == "python-package":
            groups["python"][key] = artifact
        elif (
            entry["owner"] == "agentbot"
            and entry["kind"] == "git-source"
            and key.startswith("skill-source.")
        ):
            groups["skill"][key.removeprefix("skill-source.")] = (entry, artifact)
        elif entry["owner"] == "agentbot" and key == "python_runtime":
            groups["sdk"][key] = artifact
        elif entry["owner"] == "agentbot" and key == "skills_cli":
            groups["tooling"][key] = artifact
        elif entry["owner"] == "dotfiles" and (
            entry["kind"] == "vendor-apt" or artifact.get("archive") == "deb"
        ):
            groups["vendor"][key] = artifact
        elif entry["owner"] == "dotfiles" and artifact.get("binary") == "fonts":
            groups["font"][key] = artifact
        elif entry["owner"] == "dotfiles" and artifact.get("archive") == "oci":
            _require(key == "portainer", "unsupported managed OCI smoke coverage")
            groups["oci"][key] = artifact
        elif entry["owner"] == "dotfiles" and entry["kind"] in {"software", "git-source"}:
            groups["user"][key] = artifact
        else:
            raise CatalogError(f"no smoke coverage adapter for {key}")
        observations[observation] = {
            "version": artifact.get("revision", artifact.get("version")),
            "integrity": artifact.get("digest", artifact.get("revision")),
            "owned": True,
            "managed_path": True,
        }
    return groups, observations


def audit_smoke(snapshot, receipts):
    """Require every versioned Linux component; explicitly leave upgrades unqualified."""
    _require(
        tuple(snapshot.data["required_targets"]) == ("linux-amd64",),
        "smoke audit supports the selected Linux AMD64 matrix only",
    )
    by_scope = {}
    for receipt in receipts:
        _require(isinstance(receipt, dict), "smoke receipt must be an object")
        scope = receipt.get("scope")
        _require(
            isinstance(scope, str) and scope in SCOPES and scope not in by_scope,
            "unknown or duplicate smoke scope",
        )
        _require(
            type(receipt.get("schema_version")) is int
            and receipt["schema_version"] == 1
            and receipt.get("approved") is False
            and receipt.get("catalog_digest") == snapshot.digest
            and receipt.get("sources") == dict(snapshot.data["sources"]),
            "smoke receipt uses different data, sources or approval state",
        )
        by_scope[scope] = receipt
    _require(set(by_scope) == SCOPES, "missing required smoke scope")
    user = by_scope["user-tools-smoke-only"]
    vendor = by_scope["vendor-package-smoke-only"]
    tooling = by_scope["tooling-smoke-only"]
    integration = by_scope["integration-smoke-only"]
    fonts = by_scope["linux-font-smoke-only"]
    docker = by_scope["docker-lifecycle-smoke-only"]
    groups, observations = _partition(snapshot)
    _phase_versions(user, groups["user"])
    _phase_versions(vendor, groups["vendor"], vendor=True, digest=snapshot.digest)
    for name in ("powershell", "obsidian"):
        if name in groups["vendor"]:
            probe = vendor.get("application_probes", {}).get(name, {})
            field = "local_pipeline_json" if name == "powershell" else "xvfb_visible_window"
            _successes(probe, (field,))
    _successes(tooling, ("fresh", "repeat"))
    for artifact in groups["sdk"].values():
        proof = _mapping(tooling.get("python_runtime", {}))
        expected = {
            "version": artifact["version"],
            "artifact_digest": artifact["digest"],
            "identity": hashlib.sha256(
                json.dumps(dict(artifact), sort_keys=True).encode()
            ).hexdigest(),
            "actual_version": artifact["version"].split("+", 1)[0],
            "implementation": "cpython",
            "owned": True,
            "fresh": "success",
            "repeat": "success",
        }
        _require(
            proof == expected and proof.get("owned") is True,
            "private Python runtime differs from its selected owned build",
        )
    development = _mapping(tooling.get("development", {}))
    _mapping(development.get("packages", {}))
    _successes(development, ("fresh", "repeat", "ruff", "mypy", "python_suite"))
    for artifact in groups["python"].values():
        lock = validate_python_lock(_read(snapshot.directory / artifact["lock"], 16_777_216))
        _require(
            all(
                development.get("packages", {}).get(name) == proof["version"]
                for name, proof in lock.items()
            ),
            "development package closure differs from the catalog",
        )
    for artifact in groups["tooling"].values():
        _require(
            tooling.get("skills_cli_version") == artifact["version"],
            "Skills CLI version differs from the catalog",
        )
    _successes(integration, ("fresh", "repeat"))
    _extension_evidence(snapshot, groups["extension"], integration)
    _skill_evidence(groups["skill"], integration)
    _require(len(groups["font"]) <= 1, "unsupported additional font smoke adapter")
    _successes(fonts, ("fresh", "repeat"))
    for artifact in groups["font"].values():
        _require(
            fonts.get("version") == artifact["version"]
            and isinstance(fonts.get("fontconfig_match"), str)
            and Path(fonts["fontconfig_match"]).is_absolute(),
            "font version/registration evidence differs from the catalog",
        )
    behavior = by_scope["behavior-smoke-only"].get("checks")
    _require(
        isinstance(behavior, dict) and set(behavior) == _BEHAVIOR,
        "incomplete representative behavior coverage",
    )
    _successes(behavior, _BEHAVIOR)
    _successes(docker, ("fresh", "repeat", "failed_replacement_recovery", "local_status_api"))
    _require(
        docker.get("volume_data_preserved") is True and docker.get("upgrade_qualified") is False,
        "incomplete Docker recovery evidence or unsupported upgrade claim",
    )
    if groups["oci"]:
        artifact = groups["oci"]["portainer"]
        _require(
            docker.get("packages")
            == {
                key: value["version"]
                for key, value in groups["vendor"].items()
                if key.startswith("docker.")
            }
            and _mapping(docker.get("portainer", {})).get("reference")
            == artifact["package"] + "@" + artifact["digest"],
            "daemon packages or Portainer image differ from the catalog",
        )
    return {
        "schema_version": 1,
        "scope": "fresh-repeat-inventory-audit",
        "approved": False,
        "catalog_digest": snapshot.digest,
        "sources": dict(snapshot.data["sources"]),
        "target": "linux-amd64",
        "observations": observations,
        "scenarios": {
            "fresh": "success",
            "repeat": "success",
            "upgrade": "unqualified",
            "failure-safety": "partial",
        },
        "checks": {
            "behavior": "success",
            "product-gates": "unqualified",
            "updater-controls": "partial",
        },
        "required_next": [
            "baseline-upgrade",
            "complete-failure-safety",
            "full-product-gates",
            "all-updater-controls",
        ],
    }
