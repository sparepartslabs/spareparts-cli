"""Verified, atomic marketplace installation and Codex delegation."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, os, shutil, subprocess, tarfile, tempfile
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.request import Request, urlopen
from .catalog import PluginEntry

MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_BYTES = 50 * 1024 * 1024
MAX_MEMBERS = 512

class InstallError(RuntimeError):
    def __init__(self, phase: str, detail: str): self.phase = phase; super().__init__(f"{phase}: {detail}")

@dataclass(frozen=True)
class InstallResult:
    entry: PluginEntry
    outcome: str
    marketplace_root: Path

def cache_root(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    if env.get("XDG_CACHE_HOME"): return Path(env["XDG_CACHE_HOME"]).expanduser()/"spareparts"/"plugins"
    if os.name == "nt" and env.get("LOCALAPPDATA"): return Path(env["LOCALAPPDATA"])/"spareparts"/"plugins"
    return Path(env.get("HOME", str(Path.home()))).expanduser()/".cache"/"spareparts"/"plugins"

def _download(url: str, destination: Path, *, limit: int = MAX_ARCHIVE_BYTES) -> None:
    try:
        with urlopen(Request(url, headers={"User-Agent":"spareparts-cli"}), timeout=30) as response, destination.open("wb") as output:
            length = response.headers.get("Content-Length")
            if length and int(length) > limit: raise InstallError("download", f"archive exceeds {limit} bytes")
            total = 0
            while chunk := response.read(65536):
                total += len(chunk)
                if total > limit: raise InstallError("download", f"archive exceeds {limit} bytes")
                output.write(chunk)
    except InstallError: raise
    except Exception as exc: raise InstallError("download", str(exc)) from exc

def _verify_digest(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1048576), b""): digest.update(chunk)
    except OSError as exc: raise InstallError("integrity", str(exc)) from exc
    if digest.hexdigest() != expected: raise InstallError("integrity", "archive SHA-256 does not match the catalog")

def _validated_members(archive: tarfile.TarFile, root: str) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    if len(members) > MAX_MEMBERS: raise InstallError("archive", f"archive contains more than {MAX_MEMBERS} members")
    total = 0
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or not path.parts or path.parts[0] != root or ".." in path.parts: raise InstallError("archive", f"unsafe or unexpected path: {member.name}")
        if not (member.isfile() or member.isdir()): raise InstallError("archive", f"unsupported member type: {member.name}")
        total += member.size
        if member.size > MAX_EXTRACTED_BYTES or total > MAX_EXTRACTED_BYTES: raise InstallError("archive", "extracted content exceeds the size limit")
    return members

def _read_json(path: Path, phase: str) -> dict:
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise InstallError(phase, f"invalid {path.name}: {exc}") from exc
    if not isinstance(value, dict): raise InstallError(phase, f"{path.name} must contain an object")
    return value

def _validate_manifests(root: Path, entry: PluginEntry) -> None:
    marketplace = _read_json(root/"marketplace.json", "archive")
    if marketplace.get("name") != entry.marketplace: raise InstallError("archive", "marketplace identity does not match")
    plugins = marketplace.get("plugins")
    expected_source = {"source": "local", "path": f"./plugins/{entry.name}"}
    if not isinstance(plugins, list) or not any(isinstance(p, dict) and p.get("name") == entry.name and p.get("source") == expected_source for p in plugins): raise InstallError("archive", "marketplace does not declare the expected plugin source")
    plugin_root = root/"plugins"/entry.name
    manifest = _read_json(plugin_root/".codex-plugin"/"plugin.json", "archive")
    if manifest.get("name") != entry.name or manifest.get("version") != entry.version: raise InstallError("archive", "plugin manifest identity or version does not match")
    try: skill = (plugin_root/"skills"/"lgtm"/"SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc: raise InstallError("archive", f"missing or invalid LGTM skill: {exc}") from exc
    if not skill.strip() or "PLACEHOLDER" in skill.upper(): raise InstallError("archive", "LGTM skill is empty or a placeholder")
    try: command = (plugin_root/"commands"/"lgtm.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc: raise InstallError("archive", f"missing or invalid canonical LGTM command: {exc}") from exc
    if not command.strip() or "PLACEHOLDER" in command.upper(): raise InstallError("archive", "canonical LGTM command is empty or a placeholder")

def _activate(archive_path: Path, entry: PluginEntry, base: Path) -> Path:
    versions = base/"versions"; target = versions/f"{entry.marketplace}-{entry.version}-{entry.sha256[:12]}"
    if target.exists(): _validate_manifests(target, entry); return target
    try:
        versions.mkdir(parents=True, exist_ok=True); staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=versions))
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                members = _validated_members(archive, entry.archive_root)
                for member in members:
                    destination = staging.joinpath(*PurePosixPath(member.name).parts)
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None: raise InstallError("archive", f"could not read {member.name}")
                    with source, destination.open("xb") as output: shutil.copyfileobj(source, output)
            extracted = staging/entry.archive_root; _validate_manifests(extracted, entry)
            try: extracted.replace(target)
            except FileExistsError: _validate_manifests(target, entry)
            return target
        finally: shutil.rmtree(staging, ignore_errors=True)
    except InstallError: raise
    except (OSError, tarfile.TarError) as exc: raise InstallError("cache", str(exc)) from exc

def _receipt_path(base: Path, name: str) -> Path: return base/"receipts"/f"{name}.json"
def _load_receipt(path: Path) -> dict | None:
    if not path.exists(): return None
    value = _read_json(path, "cache")
    if set(value) != {"name","marketplace","version","sha256","marketplace_root"}: raise InstallError("cache", "existing receipt is foreign or malformed; refusing to overwrite it")
    return value
def _write_receipt(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True); fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent); temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream: json.dump(data, stream, sort_keys=True); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
            temporary.replace(path)
        finally: temporary.unlink(missing_ok=True)
    except OSError as exc: raise InstallError("cache", str(exc)) from exc

def _run_codex(runner: Callable[..., subprocess.CompletedProcess], command: list[str], phase: str) -> subprocess.CompletedProcess:
    try: completed = runner(command, shell=False, text=True, capture_output=True)
    except OSError as exc: raise InstallError(phase, str(exc)) from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or f"exit {completed.returncode}").strip()
        raise InstallError(phase, detail)
    return completed

def _registered_root(completed: subprocess.CompletedProcess, marketplace: str) -> Path | None:
    try: value = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc: raise InstallError("marketplace discovery", f"Codex returned invalid JSON: {exc}") from exc
    entries = value.get("marketplaces") if isinstance(value, dict) else None
    if not isinstance(entries, list): raise InstallError("marketplace discovery", "Codex JSON does not contain a marketplaces list")
    matches = [item for item in entries if isinstance(item, dict) and item.get("name") == marketplace]
    if len(matches) > 1: raise InstallError("marketplace discovery", f"Codex returned duplicate {marketplace!r} marketplaces")
    if not matches: return None
    root = matches[0].get("root")
    if not isinstance(root, str) or not root: raise InstallError("marketplace discovery", f"Codex returned an invalid root for {marketplace!r}")
    return Path(root).expanduser().resolve()

def _register_marketplace(executable: str, marketplace_root: Path, entry: PluginEntry, receipt: dict | None, runner: Callable[..., subprocess.CompletedProcess]) -> None:
    current_root = marketplace_root.resolve()
    listed = _run_codex(runner, [executable,"plugin","marketplace","list","--json"], "marketplace discovery")
    configured_root = _registered_root(listed, entry.marketplace)
    add = [executable,"plugin","marketplace","add",str(current_root)]
    if configured_root is None:
        _run_codex(runner, add, "marketplace registration")
        return
    owned_root = None
    if receipt and isinstance(receipt.get("marketplace_root"), str):
        owned_root = Path(receipt["marketplace_root"]).expanduser().resolve()
    if configured_root == current_root:
        if owned_root != configured_root:
            raise InstallError("marketplace registration", f"{entry.marketplace!r} is already configured from an unowned root; refusing to replace it")
        return
    if owned_root != configured_root:
        raise InstallError("marketplace registration", f"{entry.marketplace!r} is already configured from foreign root {configured_root}; refusing to replace it")
    _run_codex(runner, [executable,"plugin","marketplace","remove",entry.marketplace], "marketplace removal")
    try:
        _run_codex(runner, add, "marketplace registration")
    except InstallError as error:
        try:
            _run_codex(runner, [executable,"plugin","marketplace","add",str(configured_root)], "marketplace rollback")
        except InstallError as rollback:
            raise InstallError("marketplace registration", f"{error}; rollback also failed: {rollback}") from error
        raise InstallError("marketplace registration", f"{error}; restored prior root {configured_root}") from error

def prepare(entry: PluginEntry, *, root: Path|None=None, downloader: Callable[[str, Path], None]=_download) -> Path:
    """Download, verify, and atomically cache a producer marketplace."""
    base = root or cache_root()
    try:
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="download-", dir=base) as temporary:
            archive_path = Path(temporary)/"marketplace.tar.gz"
            try: downloader(entry.archive_url, archive_path)
            except InstallError: raise
            except Exception as exc: raise InstallError("download", str(exc)) from exc
            _verify_digest(archive_path, entry.sha256)
            return _activate(archive_path, entry, base)
    except InstallError: raise
    except OSError as exc: raise InstallError("cache", str(exc)) from exc

def command_asset(marketplace_root: Path, entry: PluginEntry) -> str:
    path = marketplace_root/"plugins"/entry.name/"commands"/"lgtm.md"
    try: return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc: raise InstallError("archive", f"could not read canonical LGTM command: {exc}") from exc

def install(entry: PluginEntry, *, refresh: bool=False, root: Path|None=None, downloader: Callable[[str, Path], None]=_download, codex: str|None=None, runner: Callable[..., subprocess.CompletedProcess]=subprocess.run) -> InstallResult:
    base = root or cache_root(); receipt_path = _receipt_path(base, entry.name); receipt = _load_receipt(receipt_path)
    expected = {"name":entry.name,"marketplace":entry.marketplace,"version":entry.version,"sha256":entry.sha256}
    if receipt and (receipt.get("name") != entry.name or receipt.get("marketplace") != entry.marketplace): raise InstallError("cache", "existing receipt belongs to foreign state; refusing to overwrite it")
    executable = codex or shutil.which("codex")
    if not executable: raise InstallError("codex availability", "Codex CLI was not found on PATH")
    marketplace_root = prepare(entry, root=base, downloader=downloader)
    _register_marketplace(executable, marketplace_root, entry, receipt, runner)
    _run_codex(runner, [executable,"plugin","add",entry.install_identity], "plugin installation")
    same = receipt is not None and all(receipt.get(k) == v for k,v in expected.items())
    outcome = "refreshed" if refresh or (receipt is not None and not same) else ("unchanged" if same else "installed")
    _write_receipt(receipt_path, {**expected,"marketplace_root":str(marketplace_root.resolve())})
    return InstallResult(entry, outcome, marketplace_root)
