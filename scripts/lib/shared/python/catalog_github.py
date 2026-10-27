"""Bounded, read-only GitHub observations for catalog qualification."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import selectors
import stat
import subprocess
import time
import zipfile
from dataclasses import dataclass

from software_catalog import CatalogError, _object

REPOSITORY = "PamuduW/dotfiles-shared"
PREFIX = f"repos/{REPOSITORY}"
MAX_JSON = 8_388_608
MAX_ARCHIVE = 8_388_608
MAX_RECEIPT = 4_194_304
SHA = re.compile(r"[0-9a-f]{40}\Z")


class GitHubError(CatalogError):
    """Remote evidence is incomplete, unsafe or no longer current."""


def decode_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_object)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise GitHubError("invalid or duplicate-key GitHub evidence JSON") from error


class GhApi:
    """Only GET is available; credentials stay in gh's environment/configuration."""

    def read(self, endpoint, *, maximum=MAX_JSON):
        if (
            not isinstance(endpoint, str)
            or not re.fullmatch(
                r"repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_./?=&-]+|repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
                endpoint,
            )
            or any(part in {".", "..", ""} for part in endpoint.split("?", 1)[0].split("/"))
            or (
                "?" in endpoint
                and not re.fullmatch(
                    r"(?:per_page=100&page=[1-9][0-9]?|recursive=1)", endpoint.split("?", 1)[1]
                )
            )
            or not 0 < maximum <= MAX_JSON
        ):
            raise GitHubError("invalid read-only GitHub endpoint or bound")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"GH_DEBUG", "DEBUG", "GH_HOST"}
        }
        environment.update(GH_PROMPT_DISABLED="1", GH_PAGER="cat")
        command = [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            "GET",
            "-H",
            "Accept: application/vnd.github+json",
            endpoint,
        ]
        try:
            with subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment
            ) as process:
                output = bytearray()
                deadline = time.monotonic() + 45
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0 or not selector.select(remaining):
                                raise GitHubError("GitHub read timed out")
                            chunk = os.read(
                                process.stdout.fileno(), min(65_536, maximum + 1 - len(output))
                            )
                            if not chunk:
                                break
                            output.extend(chunk)
                            if len(output) > maximum:
                                raise GitHubError("GitHub response exceeds size limit")
                    if process.wait(timeout=max(0.01, deadline - time.monotonic())):
                        raise GitHubError("GitHub GET failed; evidence unavailable")
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                return bytes(output)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitHubError("GitHub reader unavailable or timed out") from error

    def json(self, endpoint):
        return decode_json(self.read(endpoint))

    def pages(self, endpoint, key):
        collected = []
        total = None
        for page in range(1, 21):
            data = self.json(f"{endpoint}?per_page=100&page={page}")
            if (
                not isinstance(data, dict)
                or type(data.get("total_count")) is not int
                or not isinstance(data.get(key), list)
            ):
                raise GitHubError("invalid paginated GitHub response")
            count = data["total_count"]
            if count < 0 or count > 2000 or (total is not None and total != count):
                raise GitHubError("GitHub pagination count changed or exceeds limit")
            total = count
            entries = data[key]
            if len(entries) > 100 or any(not isinstance(item, dict) for item in entries):
                raise GitHubError("invalid GitHub page entries")
            collected.extend(entries)
            if len(collected) == total:
                return collected
            if len(collected) > total or len(entries) != 100:
                raise GitHubError("incomplete GitHub pagination")
        raise GitHubError("GitHub pagination exceeds limit")


@dataclass(frozen=True)
class RunIdentity:
    repository_id: int
    workflow_id: int
    run_id: int
    attempt: int
    candidate: str
    branch: str
    workflow_revision: str

    def __post_init__(self):
        if (
            any(
                type(value) is not int or value <= 0
                for value in (self.repository_id, self.workflow_id, self.run_id, self.attempt)
            )
            or not isinstance(self.candidate, str)
            or not SHA.fullmatch(self.candidate)
            or not isinstance(self.workflow_revision, str)
            or not SHA.fullmatch(self.workflow_revision)
            or not isinstance(self.branch, str)
            or not re.fullmatch(r"catalog-candidate/[A-Za-z0-9_-]{1,100}", self.branch)
        ):
            raise GitHubError("invalid trusted qualification identity")


def validate_run(run, expected):
    if not isinstance(run, dict):
        raise GitHubError("invalid workflow run")
    fields = {
        "id": expected.run_id,
        "run_attempt": expected.attempt,
        "workflow_id": expected.workflow_id,
        "path": ".github/workflows/catalog-candidate.yml",
        "head_sha": expected.candidate,
        "head_branch": expected.branch,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
    }
    if any(run.get(key) != value for key, value in fields.items()):
        raise GitHubError("qualification run identity, attempt or conclusion differs")
    for key in ("repository", "head_repository"):
        value = run.get(key)
        if not isinstance(value, dict) or value.get("id") != expected.repository_id:
            raise GitHubError("qualification repository differs")
    if run["repository"].get("full_name") != REPOSITORY:
        raise GitHubError("qualification repository authority differs")
    workflows = run.get("referenced_workflows")
    path = f"{REPOSITORY}/.github/workflows/catalog-qualify.yml@{expected.workflow_revision}"
    if (
        not isinstance(workflows, list)
        or len(workflows) != 1
        or not isinstance(workflows[0], dict)
        or workflows[0].get("path") != path
        or workflows[0].get("sha") != expected.workflow_revision
    ):
        raise GitHubError("qualification did not use the trusted pinned workflow")


def validate_jobs(jobs, expected, required):
    if (
        not isinstance(required, dict)
        or not required
        or any(
            not isinstance(name, str)
            or not isinstance(steps, list)
            or not steps
            or any(not isinstance(step, str) or not step for step in steps)
            or len(set(steps)) != len(steps)
            for name, steps in required.items()
        )
    ):
        raise GitHubError("trusted required job/step policy is empty or invalid")
    if not isinstance(jobs, list) or len(jobs) != len(required):
        raise GitHubError("missing or extra qualification jobs")
    found = set()
    identifiers = set()
    for job in jobs:
        if not isinstance(job, dict):
            raise GitHubError("invalid qualification job")
        name = job.get("name")
        identifier = job.get("id")
        if (
            not isinstance(name, str)
            or name not in required
            or name in found
            or type(identifier) is not int
            or identifier in identifiers
        ):
            raise GitHubError("missing, duplicate or unexpected qualification job")
        found.add(name)
        identifiers.add(identifier)
        fields = {
            "run_id": expected.run_id,
            "run_attempt": expected.attempt,
            "head_sha": expected.candidate,
            "status": "completed",
            "conclusion": "success",
        }
        labels, runner = job.get("labels"), job.get("runner_name")
        if (
            any(job.get(key) != value for key, value in fields.items())
            or not isinstance(labels, list)
            or "ubuntu-24.04" not in labels
            or not isinstance(runner, str)
            or not runner
        ):
            raise GitHubError("qualification job did not succeed on the frozen run and runner")
        steps = job.get("steps")
        if not isinstance(steps, list):
            raise GitHubError("qualification steps are missing")
        for required_step in required[name]:
            matches = [
                step
                for step in steps
                if isinstance(step, dict) and step.get("name") == required_step
            ]
            if (
                len(matches) != 1
                or matches[0].get("status") != "completed"
                or matches[0].get("conclusion") != "success"
            ):
                raise GitHubError("required qualification step did not succeed exactly once")


def read_receipt_zip(raw, digest):
    if len(raw) > MAX_ARCHIVE or digest != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise GitHubError("artifact archive size or digest differs")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != "receipt.json":
                raise GitHubError("artifact must contain only receipt.json")
            entry = entries[0]
            mode = entry.external_attr >> 16
            if (
                stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                or mode & 0o111
                or entry.is_dir()
                or entry.flag_bits & 1
                or entry.file_size > MAX_RECEIPT
                or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            ):
                raise GitHubError("unsafe artifact entry mode, compression or size")
            with archive.open(entry) as stream:
                content = stream.read(MAX_RECEIPT + 1)
            if len(content) != entry.file_size or len(content) > MAX_RECEIPT:
                raise GitHubError("artifact receipt size differs")
            result = decode_json(content)
            if not isinstance(result, dict):
                raise GitHubError("artifact receipt must be an object")
            return result
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as error:
        raise GitHubError("invalid artifact ZIP") from error


def read_qualification(api, identity, required_jobs, targets, predecessor):
    root = f"{PREFIX}/actions/runs/{identity.run_id}"
    validate_run(api.json(root), identity)
    validate_run(api.json(f"{root}/attempts/{identity.attempt}"), identity)
    jobs = api.pages(f"{root}/attempts/{identity.attempt}/jobs", "jobs")
    validate_jobs(jobs, identity, required_jobs)
    artifacts = api.pages(f"{root}/artifacts", "artifacts")
    receipts, evidence = [], []
    for target in targets:
        name = f"qualification-{target}-{identity.run_id}-{identity.attempt}"
        matches = [item for item in artifacts if item.get("name") == name]
        if len(matches) != 1:
            raise GitHubError("complete qualification artifact missing or duplicated")
        artifact = matches[0]
        size, identifier = artifact.get("size_in_bytes"), artifact.get("id")
        if (
            type(identifier) is not int
            or identifier <= 0
            or type(size) is not int
            or not 0 < size <= MAX_ARCHIVE
            or artifact.get("expired") is not False
        ):
            raise GitHubError("qualification artifact is expired or outside bounds")
        run = artifact.get("workflow_run")
        fields = {
            "id": identity.run_id,
            "repository_id": identity.repository_id,
            "head_repository_id": identity.repository_id,
            "head_sha": identity.candidate,
            "head_branch": identity.branch,
        }
        if not isinstance(run, dict) or any(run.get(key) != value for key, value in fields.items()):
            raise GitHubError("artifact origin differs from the qualified candidate")
        raw = api.read(f"{PREFIX}/actions/artifacts/{identifier}/zip", maximum=MAX_ARCHIVE)
        if len(raw) != size:
            raise GitHubError("artifact archive size changed")
        envelope = read_receipt_zip(raw, artifact.get("digest"))
        if (
            set(envelope)
            != {
                "schema_version",
                "candidate_commit",
                "workflow_revision",
                "run_attempt",
                "approved_predecessor",
                "receipt",
            }
            or type(envelope.get("schema_version")) is not int
            or envelope["schema_version"] != 1
            or envelope["candidate_commit"] != identity.candidate
            or envelope["workflow_revision"] != identity.workflow_revision
            or type(envelope["run_attempt"]) is not int
            or envelope["run_attempt"] != identity.attempt
            or envelope["approved_predecessor"] != predecessor
            or not isinstance(envelope["receipt"], dict)
            or envelope["receipt"].get("target") != target
        ):
            raise GitHubError("qualification receipt envelope identity differs")
        receipts.append(envelope["receipt"])
        evidence.append({"id": identifier, "digest": artifact["digest"], "name": name})
    validate_run(api.json(root), identity)
    return receipts, {
        "run_id": identity.run_id,
        "run_attempt": identity.attempt,
        "candidate_commit": identity.candidate,
        "workflow_revision": identity.workflow_revision,
        "job_ids": sorted(job["id"] for job in jobs),
        "artifacts": evidence,
    }
