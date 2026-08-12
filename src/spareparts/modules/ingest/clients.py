"""Small bounded HTTP clients for GitHub evidence and Core persistence."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .models import IngestionError

Transport = Callable[[urllib.request.Request], tuple[int, Any]]


def _transport(request: urllib.request.Request) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        return err.code, body
    except (urllib.error.URLError, TimeoutError) as err:
        raise IngestionError(f"network request failed: {err.reason if hasattr(err, 'reason') else err}") from err


class GitHubClient:
    def __init__(self, token: str, transport: Transport = _transport):
        if not token:
            raise IngestionError("GitHub ingestion needs GITHUB_TOKEN set")
        self.token = token
        self.transport = transport

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = "?" + urllib.parse.urlencode(params) if params else ""
        request = urllib.request.Request(
            "https://api.github.com" + path + query,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
        status, body = self.transport(request)
        if status < 200 or status >= 300:
            raise IngestionError(f"GitHub API returned HTTP {status} for {path}")
        return body

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        request = urllib.request.Request(
            "https://api.github.com" + path, method=method, data=data,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"},
        )
        status, value = self.transport(request)
        if status < 200 or status >= 300:
            raise IngestionError(f"GitHub API returned HTTP {status} for {path}")
        return value

    def repositories(self, organization: str, limit: int) -> list[dict[str, Any]]:
        values = self._get(f"/orgs/{urllib.parse.quote(organization)}/repos", {"per_page": limit, "sort": "full_name"})
        if not isinstance(values, list):
            raise IngestionError("GitHub repository response was not an array")
        return [self._repository(repo) for repo in values[:limit]]

    @staticmethod
    def _repository(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or not value.get("node_id") or not value.get("full_name"):
            raise IngestionError("GitHub returned an invalid repository")
        return {
            "repository_id": str(value["node_id"]), "name_with_owner": value["full_name"],
            "name": value.get("name") or value["full_name"].split("/")[-1],
            "description": value.get("description") or "", "source_url": value.get("html_url") or "",
            "visibility": value.get("visibility") or ("private" if value.get("private") else "public"),
            "lifecycle_state": "archived" if value.get("archived") else ("inaccessible" if value.get("disabled") else "active"),
            "is_archived": bool(value.get("archived")),
            "relationships": ([{"kind": "fork_of", "repository_id": value["parent"]["node_id"]}]
                              if isinstance(value.get("parent"), dict) and value["parent"].get("node_id") else []),
            "metadata": {
                "fork": bool(value.get("fork")), "default_branch": value.get("default_branch"),
                "topics": value.get("topics") if isinstance(value.get("topics"), list) else [],
                "language": value.get("language"),
            },
        }

    def upsert_issue_summary(self, full_name: str, issue_number: int, marker: str, body: str) -> dict[str, Any]:
        owner, repo = full_name.split("/", 1)
        base = f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
        viewer = self._request("GET", "/user")
        login = viewer.get("login") if isinstance(viewer, dict) else None
        if not isinstance(login, str) or not login:
            raise IngestionError("GitHub returned no authenticated login")
        comments = self._get(base + f"/issues/{issue_number}/comments", {"per_page": 100})
        if not isinstance(comments, list):
            raise IngestionError("GitHub comments response was not an array")
        for comment in comments:
            author = comment.get("user", {}).get("login") if isinstance(comment, dict) else None
            if author == login and marker in str(comment.get("body", "")) and comment.get("id"):
                value = self._request("PATCH", base + f"/issues/comments/{comment['id' ]}", {"body": body})
                return {"action": "updated", "comment_id": value.get("id"), "url": value.get("html_url")}
        value = self._request("POST", base + f"/issues/{issue_number}/comments", {"body": body})
        return {"action": "created", "comment_id": value.get("id"), "url": value.get("html_url")}

    def approved_reviews(self, full_name: str, max_pulls: int = 100, max_reviews: int = 100) -> list[dict[str, Any]]:
        owner, repo = full_name.split("/", 1)
        base = f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
        pulls = self._get(base + "/pulls", {"state": "all", "sort": "updated", "direction": "desc", "per_page": max_pulls})
        if not isinstance(pulls, list):
            raise IngestionError("GitHub pull response was not an array")
        reviews: list[dict[str, Any]] = []
        for pull in pulls[:max_pulls]:
            if not isinstance(pull, dict) or not isinstance(pull.get("number"), int):
                continue
            values = self._get(base + f"/pulls/{pull['number']}/reviews", {"per_page": max_reviews})
            if isinstance(values, list):
                reviews.extend(item for item in values[:max_reviews] if isinstance(item, dict))
        return reviews


class CoreClient:
    def __init__(self, base_url: str, token: str, transport: Transport = _transport):
        if not base_url:
            raise IngestionError("--core-url is required")
        if not token:
            raise IngestionError("Core ingestion needs SPAREPARTS_INGEST_KEY set")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport

    def _request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        request = urllib.request.Request(
            self.base_url + path, method=method, data=data,
            headers={"x-api-key": self.token, "Content-Type": "application/json"},
        )
        return self.transport(request)

    def current_ontology(self, organization: str, limit: int) -> dict[str, Any] | None:
        query = urllib.parse.urlencode({"organization_login": organization, "q": "", "limit": limit, "stale_after_hours": 24})
        status, body = self._request("GET", "/ingestion/v1/ontology-context?" + query)
        if status == 404 or (status == 200 and isinstance(body, dict) and body.get("revision") is None):
            return None
        if status < 200 or status >= 300:
            raise IngestionError(f"Core ontology request returned HTTP {status}")
        return self._ontology(body)

    @staticmethod
    def _ontology(body: Any) -> dict[str, Any]:
        if isinstance(body, dict) and isinstance(body.get("revision"), dict):
            revision = dict(body["revision"])
            revision["repositories"] = body.get("repositories", [])
            revision["complete"] = True
            return revision
        if not isinstance(body, dict):
            raise IngestionError("Core ontology response was not an object")
        return body

    def create_ontology(self, body: dict[str, Any]) -> dict[str, Any]:
        status, response = self._request("POST", "/ingestion/v1/ontology-revisions", body)
        if status < 200 or status >= 300:
            raise IngestionError(f"Core ontology creation returned HTTP {status}")
        if not isinstance(response, dict) or not isinstance(response.get("id"), str):
            raise IngestionError("Core ontology creation response was invalid")
        return {**response, "revision_id": response["id"], "complete": True, "repositories": body["repositories"]}

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        status, response = self._request("POST", "/ingestion/v1/issues", body)
        if status < 200 or status >= 300:
            raise IngestionError(f"Core ingestion submission returned HTTP {status}")
        if not isinstance(response, dict):
            raise IngestionError("Core ingestion response was not an object")
        return response
