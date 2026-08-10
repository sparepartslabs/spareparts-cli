from __future__ import annotations
import hashlib, io, json, tarfile
from pathlib import Path
from spareparts.modules.plugin.catalog import PluginEntry

def archive_bytes(*, root="marketplace-1.2.3", version="1.2.3", extra=None) -> bytes:
    content = {
        f"{root}/marketplace.json": json.dumps({"name":"sparepartslabs","plugins":[{"name":"lgtm","source":{"source":"local","path":"./plugins/lgtm"}}]}).encode(),
        f"{root}/plugins/lgtm/.codex-plugin/plugin.json": json.dumps({"name":"lgtm","version":version}).encode(),
        f"{root}/plugins/lgtm/skills/lgtm/SKILL.md": b"---\nname: lgtm\ndescription: Review changes\n---\nRun LGTM.\n",
        f"{root}/plugins/lgtm/commands/lgtm.md": b"---\ndescription: Run LGTM\n---\n\nReview $ARGUMENTS with LGTM.\n",
    }
    content.update(extra or {})
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in content.items():
            info = tarfile.TarInfo(name); info.size = len(data); archive.addfile(info, io.BytesIO(data))
    return output.getvalue()

def fixture_entry(data: bytes, *, version="1.2.3", root="marketplace-1.2.3") -> PluginEntry:
    return PluginEntry("lgtm","sparepartslabs",version,"https://example.test/lgtm.tar.gz",hashlib.sha256(data).hexdigest(),root,"lgtm@sparepartslabs")

def downloader(data: bytes):
    def write(_url: str, path: Path): path.write_bytes(data)
    return write
