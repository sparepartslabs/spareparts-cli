"""Injected HTTP and argv-only subprocess boundaries."""

from __future__ import annotations

import json
import os
import base64
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .models import BuildError


@dataclass(frozen=True)
class CommandResult:
    code: int
    stdout: str
    stderr: str


class Commands:
    def __call__(self, argv: list[str], *, cwd: Path | None = None, timeout: int = 300, input_text: str | None = None, env: dict[str, str] | None = None) -> CommandResult:
        try:
            result = subprocess.run(argv, cwd=cwd, input=input_text, text=True, capture_output=True, timeout=timeout, env=env or os.environ.copy(), check=False)
        except subprocess.TimeoutExpired as error:
            raise BuildError(f"command timed out after {timeout}s: {argv[0]}", "retryable_failure") from error
        return CommandResult(result.returncode, result.stdout[-20000:], result.stderr[-20000:])


Transport = Callable[[urllib.request.Request], tuple[int, Any]]


def transport(request: urllib.request.Request) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read()
        try: body = json.loads(raw) if raw else {}
        except json.JSONDecodeError: body = {}
        return error.code, body
    except (urllib.error.URLError, TimeoutError) as error:
        raise BuildError(f"Core request failed: {getattr(error, 'reason', error)}", "retryable_failure") from error


class CoreClient:
    def __init__(self, url: str, key: str, request: Transport = transport):
        if not url: raise BuildError("--core-url is required")
        if not key: raise BuildError("SPAREPARTS_API_KEY is required")
        self.url, self.key, self.request = url.rstrip("/"), key, request

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method, headers={"x-api-key": self.key, "content-type": "application/json"})
        status, response = self.request(request)
        if not 200 <= status < 300:
            category = "retryable_failure" if status >= 500 or status == 429 else "permanent_failure"
            raise BuildError(f"Core returned HTTP {status} for {path}", category)
        return response

    def latest(self, repository: str, issue: int) -> Any:
        query = urllib.parse.urlencode({"source_repository": repository, "issue_number": issue})
        return self._call("GET", "/ingestion/v1/issues/latest?" + query)

    def create_attempt(self, body: dict[str, Any]) -> dict[str, Any]:
        value = self._call("POST", "/ingestion/v1/build-attempts", body)
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise BuildError("Core build attempt response was invalid", "retryable_failure")
        return value

    def update_attempt(self, attempt_id: str, body: dict[str, Any]) -> dict[str, Any]:
        value = self._call("PATCH", "/ingestion/v1/build-attempts/" + urllib.parse.quote(attempt_id, safe=""), body)
        if not isinstance(value, dict): raise BuildError("Core update response was invalid", "retryable_failure")
        return value


class GitHub:
    def __init__(self, commands: Commands, token: str):
        if not token: raise BuildError("GITHUB_TOKEN is required")
        self.commands, self.env = commands, {**os.environ, "GH_TOKEN": token}
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        self.git_env = {
            **os.environ,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
            "GIT_TERMINAL_PROMPT": "0",
        }

    def _run(self, argv: list[str], cwd: Path | None = None, timeout: int = 300) -> str:
        result = self.commands(argv, cwd=cwd, timeout=timeout, env=self.env)
        if result.code: raise BuildError(f"{argv[0]} {argv[1]} failed: {result.stderr.strip()[:1000]}", "retryable_failure")
        return result.stdout.strip()

    def metadata(self, repository: str) -> dict[str, Any]:
        text = self._run(["gh", "api", f"repos/{repository}"])
        try: value = json.loads(text)
        except json.JSONDecodeError as error: raise BuildError("GitHub metadata was invalid", "retryable_failure") from error
        if not isinstance(value, dict): raise BuildError("GitHub metadata was invalid", "retryable_failure")
        return value

    def clone(self, repository: str, path: Path) -> None:
        self._run(["gh", "repo", "clone", repository, str(path), "--", "--filter=blob:none"])

    def find_pr(self, repository: str, branch: str) -> dict[str, Any] | None:
        text = self._run(["gh", "pr", "list", "--repo", repository, "--head", branch, "--state", "open", "--json", "number,url,body"])
        values = json.loads(text or "[]")
        return values[0] if isinstance(values, list) and values else None

    def publish_pr(self, repository: str, branch: str, base: str, title: str, body: str) -> dict[str, Any]:
        existing = self.find_pr(repository, branch)
        if existing:
            self._run(["gh", "pr", "edit", str(existing["number"]), "--repo", repository, "--title", title, "--body", body])
            return existing
        url = self._run(["gh", "pr", "create", "--repo", repository, "--head", branch, "--base", base, "--title", title, "--body", body])
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        return {"number": number, "url": url}

    def publish_status(self, repository: str, issue: int, marker: str, body: str) -> None:
        text = self._run(["gh", "api", f"repos/{repository}/issues/{issue}/comments", "--paginate", "--slurp"])
        try:
            pages = json.loads(text or "[]")
            if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
                raise ValueError("invalid comment pages")
            comments = [item for page in pages for item in page]
        except (json.JSONDecodeError, ValueError) as error:
            raise BuildError("GitHub issue comments were invalid", "retryable_failure") from error
        existing = next((item for item in comments if isinstance(item, dict) and marker in str(item.get("body", ""))), None)
        if existing:
            self._run(["gh", "api", "--method", "PATCH", f"repos/{repository}/issues/comments/{existing['id']}", "-f", f"body={body}"])
        else:
            self._run(["gh", "api", "--method", "POST", f"repos/{repository}/issues/{issue}/comments", "-f", f"body={body}"])
