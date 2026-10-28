"""Bounded App requests confined to catalog submission in the owning repository."""

from __future__ import annotations

import json
import os
import re
import selectors
import subprocess
import sys
import tempfile
import time

from catalog_github import MAX_JSON, PREFIX, SHA, GhApi, GitHubError, decode_json

_PUBLIC_REFS = frozenset(
    f"repos/PamuduW/{repository}/git/ref/heads/main" for repository in ("agentbot", "dotfiles")
)
_BRANCH = re.compile(r"catalog-publish/[0-9a-f]{64}\Z")


_ANONYMOUS_READ = r"""
import sys,urllib.request
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        raise RuntimeError('fixed public source redirected')
request=urllib.request.Request(
    'https://api.github.com/'+sys.argv[1],
    headers={'Accept':'application/vnd.github+json','User-Agent':'catalog-publisher/1'},
)
with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=30) as response:
    sys.stdout.buffer.write(response.read(int(sys.argv[2])+1))
"""


class GhAppWriter(GhApi):
    """Use an explicit installation token; scope and trusted minting remain separate gates."""

    def __init__(self):
        token = os.environ.get("GH_TOKEN", "")
        if not token or len(token) > 8192 or any(not 33 <= ord(c) <= 126 for c in token):
            raise GitHubError("publisher needs an explicit GH_TOKEN from its trusted App mint")
        self._environment = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "GH_DEBUG",
                "DEBUG",
                "GH_HOST",
                "GITHUB_TOKEN",
                "CATALOG_GITHUB_TOKEN",
                "GH_ENTERPRISE_TOKEN",
                "GITHUB_ENTERPRISE_TOKEN",
            }
        }
        self._environment.update(GH_TOKEN=token, GH_PROMPT_DISABLED="1", GH_PAGER="cat")

    def read(self, endpoint, *, maximum=MAX_JSON):
        if not isinstance(endpoint, str):
            raise GitHubError("publisher read endpoint must be a string")
        if type(maximum) is not int or not 0 < maximum <= MAX_JSON:
            raise GitHubError("invalid publisher response size limit")
        if endpoint in _PUBLIC_REFS:
            return self._run_process(
                [sys.executable, "-I", "-S", "-B", "-c", _ANONYMOUS_READ, endpoint, str(maximum)],
                b"",
                {"LANG": "C.UTF-8"},
                maximum=maximum,
                timeout=30,
            )
        if not self._read_endpoint(endpoint):
            raise GitHubError("publisher read endpoint is outside its fixed scope")
        return self._request("GET", endpoint, maximum=maximum)

    @staticmethod
    def _read_endpoint(endpoint):
        if not isinstance(endpoint, str):
            return False
        if endpoint == "installation/repositories?per_page=100&page=1":
            return True
        path, separator, query = endpoint.partition("?")
        if not re.fullmatch(re.escape(PREFIX) + r"(?:/[A-Za-z0-9_.-]+)*", path) or any(
            part in {".", ".."} for part in path.split("/")
        ):
            return False
        if not separator:
            return True
        if re.fullmatch(r"per_page=100&page=[1-9][0-9]?|recursive=1", query):
            return True
        return (
            path == PREFIX + "/pulls"
            and re.fullmatch(
                r"state=all&head=PamuduW%3Acatalog-publish%2F[0-9a-f]{64}"
                r"&per_page=100&page=1",
                query,
            )
            is not None
        )

    def post(self, endpoint, payload):
        if not self._write_payload(endpoint, payload):
            raise GitHubError("publisher mutation is outside catalog submission scope")
        return decode_json(self._request("POST", endpoint, payload=payload))

    @staticmethod
    def _write_payload(endpoint, data):
        if not isinstance(data, dict):
            return False
        if endpoint == PREFIX + "/git/refs":
            return (
                set(data) == {"ref", "sha"}
                and isinstance(data["ref"], str)
                and re.fullmatch(r"refs/heads/catalog-publish/[0-9a-f]{64}", data["ref"])
                is not None
                and isinstance(data["sha"], str)
                and SHA.fullmatch(data["sha"]) is not None
            )
        if endpoint == PREFIX + "/git/trees":
            if (
                set(data) != {"base_tree", "tree"}
                or not isinstance(data["base_tree"], str)
                or not SHA.fullmatch(data["base_tree"])
                or not isinstance(data["tree"], list)
                or not 1 <= len(data["tree"]) <= 2
            ):
                return False
            paths = set()
            for entry in data["tree"]:
                if (
                    not isinstance(entry, dict)
                    or set(entry) != {"path", "mode", "type", "content"}
                    or not isinstance(entry["path"], str)
                    or entry["path"] not in {"catalog/approval", "catalog/approval.json"}
                    or entry["path"] in paths
                    or entry["mode"] != "100644"
                    or entry["type"] != "blob"
                    or not isinstance(entry["content"], str)
                ):
                    return False
                paths.add(entry["path"])
            return True
        if endpoint == PREFIX + "/git/commits":
            return (
                set(data) == {"tree", "parents", "message"}
                and isinstance(data["tree"], str)
                and SHA.fullmatch(data["tree"]) is not None
                and isinstance(data["parents"], list)
                and len(data["parents"]) == 1
                and isinstance(data["parents"][0], str)
                and SHA.fullmatch(data["parents"][0]) is not None
                and isinstance(data["message"], str)
                and len(data["message"]) <= 4096
            )
        if endpoint == PREFIX + "/pulls":
            return (
                set(data) == {"base", "head", "title", "body", "maintainer_can_modify", "draft"}
                and isinstance(data["base"], str)
                and data["base"] in {"development", "main"}
                and isinstance(data["head"], str)
                and _BRANCH.fullmatch(data["head"]) is not None
                and isinstance(data["title"], str)
                and 0 < len(data["title"]) <= 140
                and isinstance(data["body"], str)
                and len(data["body"]) <= 16_384
                and data["maintainer_can_modify"] is False
                and data["draft"] is False
            )
        return False

    def _request(self, method, endpoint, *, payload=None, maximum=MAX_JSON):
        try:
            body = b"" if payload is None else json.dumps(payload, sort_keys=True).encode()
        except (ValueError, TypeError, UnicodeError) as error:
            raise GitHubError("invalid publisher request JSON") from error
        if len(body) > MAX_JSON:
            raise GitHubError("publisher request exceeds size limit")
        command = [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            method,
            "-H",
            "Accept: application/vnd.github+json",
            endpoint,
        ]
        if payload is not None:
            command.extend(["--input", "-"])
        return self._run_process(command, body, self._environment, maximum=maximum, timeout=45)

    @staticmethod
    def _run_process(command, body, environment, *, maximum, timeout):
        deadline = time.monotonic() + timeout
        try:
            with tempfile.TemporaryFile() as request:
                request.write(body)
                request.seek(0)
                with subprocess.Popen(
                    command,
                    stdin=request,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=environment,
                ) as process:
                    result = bytearray()
                    try:
                        with selectors.DefaultSelector() as selector:
                            selector.register(process.stdout, selectors.EVENT_READ)
                            while True:
                                remaining = deadline - time.monotonic()
                                if remaining <= 0 or not selector.select(remaining):
                                    raise GitHubError(
                                        "publisher request timed out; reconcile before retry"
                                    )
                                chunk = os.read(
                                    process.stdout.fileno(), min(65_536, maximum + 1 - len(result))
                                )
                                if not chunk:
                                    break
                                result.extend(chunk)
                                if len(result) > maximum:
                                    raise GitHubError("publisher response exceeds size limit")
                        if process.wait(timeout=max(0.01, deadline - time.monotonic())):
                            raise GitHubError("publisher request failed; reconcile before retry")
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
                    return bytes(result)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitHubError(
                "publisher unavailable or timed out; reconcile before retry"
            ) from error
