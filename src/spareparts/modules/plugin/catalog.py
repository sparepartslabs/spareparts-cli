"""Load the immutable plugin catalog shipped with this CLI."""
from __future__ import annotations
from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

class CatalogError(ValueError): pass

@dataclass(frozen=True)
class PluginEntry:
    name: str
    marketplace: str
    version: str
    archive_url: str
    sha256: str
    archive_root: str
    install_identity: str

_KEYS = {"name","marketplace","version","archive_url","sha256","archive_root","install_identity"}
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ROOT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

def _entry(raw: Any) -> PluginEntry:
    if not isinstance(raw, dict) or set(raw) != _KEYS: raise CatalogError("catalog entry must contain exactly the supported fields")
    if not all(isinstance(raw[k], str) and raw[k] for k in _KEYS): raise CatalogError("catalog fields must be non-empty strings")
    if (raw["name"], raw["marketplace"], raw["install_identity"]) != ("lgtm", "sparepartslabs", "lgtm@sparepartslabs"): raise CatalogError("catalog identity does not match the known plugin")
    if not _SEMVER.fullmatch(raw["version"]): raise CatalogError("plugin version is not semantic")
    url = urlparse(raw["archive_url"])
    if url.scheme != "https" or not url.netloc or url.username or url.password: raise CatalogError("archive URL must be HTTPS without credentials")
    if not _DIGEST.fullmatch(raw["sha256"]): raise CatalogError("archive digest must be lowercase SHA-256")
    if not _ROOT.fullmatch(raw["archive_root"]): raise CatalogError("archive root is unsafe")
    return PluginEntry(**raw)

def load_catalog(path: Path | None = None) -> tuple[PluginEntry, ...]:
    try:
        text = path.read_text(encoding="utf-8") if path else files("spareparts.modules.plugin").joinpath("catalog.json").read_text(encoding="utf-8")
        raw = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise CatalogError(f"could not read plugin catalog: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"plugins"} or not isinstance(raw["plugins"], list): raise CatalogError("catalog must contain exactly a plugins list")
    entries = tuple(_entry(item) for item in raw["plugins"])
    if not entries or len({e.name for e in entries}) != len(entries): raise CatalogError("catalog names must be unique")
    return entries

def find_plugin(name: str, path: Path | None = None) -> PluginEntry:
    for entry in load_catalog(path):
        if entry.name == name: return entry
    raise CatalogError(f"unknown plugin {name!r}")
