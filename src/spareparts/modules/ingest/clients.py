"""Small bounded HTTP clients for GitHub evidence and Core persistence."""

from __future__ import annotations

import json
import base64
import hashlib
import re
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .models import IngestionError

SUMMARY_LIMIT = 320

def _clean(value: Any, limit: int = SUMMARY_LIMIT) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"https?://\S+", "", text).replace("@", "")
    return " ".join(text.split())[:limit]

def _readme_summary(text: str, component: str) -> str:
    heading = ""
    paragraphs: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not heading and stripped.startswith("#"):
            heading = _clean(stripped.lstrip("#").strip(), 100); continue
        badge = stripped.startswith(("![", "[![", "<img", "<picture", "<!--"))
        if not stripped:
            if current: paragraphs.append(_clean(" ".join(current))); current=[]
        elif not badge and not stripped.startswith(("#", "```", "---")):
            current.append(stripped)
    if current: paragraphs.append(_clean(" ".join(current)))
    prose = next((value for value in paragraphs if value and not value.startswith("[")), "")
    return _clean(" — ".join(value for value in (heading or component, prose) if value))

def _manifest_summary(filename: str, text: str, component: str) -> tuple[str, list[str], list[str]]:
    name, description, dependencies = component, "", []
    try:
        if filename == "package.json":
            value = json.loads(text); name=_clean(value.get("name"),100) or name; description=_clean(value.get("description")); dependencies=sorted(set((value.get("dependencies") or {}) | (value.get("devDependencies") or {})))
        elif filename == "pyproject.toml":
            value=tomllib.loads(text); project=value.get("project",{}); name=_clean(project.get("name"),100) or name; description=_clean(project.get("description")); dependencies=[str(item).split(" ",1)[0].split("[",1)[0] for item in project.get("dependencies",[]) if isinstance(item,str)]
        elif filename == "Cargo.toml":
            value=tomllib.loads(text); package=value.get("package",{}); name=_clean(package.get("name"),100) or name; description=_clean(package.get("description")); dependencies=sorted((value.get("dependencies") or {}).keys())
        elif filename == "go.mod":
            match=re.search(r"(?m)^module\s+(\S+)",text); name=_clean(match.group(1),100) if match else name; dependencies=re.findall(r"(?m)^\s*([\w./-]+)\s+v\d",text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, TypeError, AttributeError):
        pass
    known={"next":"Next.js","react":"React","vue":"Vue","svelte":"Svelte","django":"Django","fastapi":"FastAPI","flask":"Flask","swiftui":"SwiftUI"}
    frameworks=sorted({label for dep in dependencies for key,label in known.items() if key in dep.casefold()})
    purpose=_clean(" — ".join(value for value in (name,description) if value)) or f"Manifest metadata for {component}."
    return purpose, dependencies[:30], frameworks

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

    def components(self, full_name: str, branch: str | None, *, max_components: int, max_requests: int, max_bytes: int) -> list[dict[str, Any]]:
        owner, repo = full_name.split("/", 1); base = f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
        tree = self._get(base + "/git/trees/" + urllib.parse.quote(branch or "HEAD", safe=""), {"recursive": "1"})
        entries = tree.get("tree", []) if isinstance(tree, dict) else []
        manifests = ("README.md", "package.json", "pyproject.toml", "Cargo.toml", "go.mod")
        paths = sorted(x["path"] for x in entries if isinstance(x, dict) and x.get("type") == "blob" and isinstance(x.get("path"), str) and (x["path"].rsplit("/",1)[-1] in manifests))[:max_requests]
        components: dict[str, dict[str, Any]] = {}
        used = 0
        for path in paths:
            value = self._get(base + "/contents/" + urllib.parse.quote(path, safe="/"), {"ref": branch or "HEAD"})
            raw = value.get("content", "") if isinstance(value, dict) else ""
            try: data = base64.b64decode(raw, validate=False)
            except Exception: data = b""
            data = data[:max(0, max_bytes-used)]; used += len(data)
            text = data.decode(errors="replace"); root = path.rsplit("/",1)[0] if "/" in path else "."
            item = components.setdefault(root, {"path": root, "kind": "package" if root != "." else "repository", "name": root.rsplit("/",1)[-1] if root != "." else repo, "description": "", "languages": [], "frameworks": [], "evidence": []})
            filename=path.rsplit("/",1)[-1]; evidence_kind = "readme" if filename.lower()=="readme.md" else "manifest"
            if evidence_kind == "readme": summary=_readme_summary(text,item["name"]); dependencies=[]
            else: summary,dependencies,frameworks=_manifest_summary(filename,text,item["name"]); item["frameworks"].extend(frameworks)
            item["evidence"].append({"kind": "readme" if path.lower().endswith("readme.md") else "manifest", "path": path, "summary": summary, "content_hash": "sha256:"+hashlib.sha256(data).hexdigest()})
            if not item["description"] and summary: item["description"] = summary
            if path.endswith("package.json"): item["languages"].append("JavaScript")
            if path.endswith("pyproject.toml"): item["languages"].append("Python")
            if used >= max_bytes: break
        result=[]
        for item in sorted(components.values(), key=lambda value: value["path"].casefold())[:max_components]:
            item["languages"] = sorted(set(item["languages"]))
            item["frameworks"] = sorted(set(item["frameworks"]))
            item["evidence"] = sorted(item["evidence"], key=lambda value: (value["path"].casefold(), value["kind"]))
            document=json.dumps({k:v for k,v in item.items() if k != "content_hash"}, sort_keys=True, separators=(",",":"))
            item["content_hash"]="sha256:"+hashlib.sha256(document.encode()).hexdigest(); result.append(item)
        return result

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
        comments: list[dict[str, Any]] = []
        page = 1
        while True:
            values = self._get(base + f"/issues/{issue_number}/comments", {"per_page": 100, "page": page})
            if not isinstance(values, list):
                raise IngestionError("GitHub comments response was not an array")
            comments.extend(item for item in values if isinstance(item, dict))
            if len(values) < 100:
                break
            page += 1
        for comment in comments:
            author = comment.get("user", {}).get("login") if isinstance(comment, dict) else None
            if isinstance(author, str) and author.casefold() == login.casefold() and marker in str(comment.get("body", "")) and comment.get("id"):
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

    def search_ontology(self, body: dict[str, Any]) -> dict[str, Any]:
        status, response = self._request("POST", "/ingestion/v1/ontology-context/search", body)
        if status < 200 or status >= 300 or not isinstance(response, dict): raise IngestionError(f"Core ontology search returned HTTP {status}")
        return response

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        status, response = self._request("POST", "/ingestion/v1/issues", body)
        if status < 200 or status >= 300:
            raise IngestionError(f"Core ingestion submission returned HTTP {status}")
        if not isinstance(response, dict):
            raise IngestionError("Core ingestion response was not an object")
        return response
