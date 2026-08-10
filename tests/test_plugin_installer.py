from __future__ import annotations
import io, json, subprocess, tarfile
from pathlib import Path
import pytest
from spareparts.modules.plugin import installer
from spareparts.modules.plugin.installer import InstallError, install
from plugin_helpers import archive_bytes, downloader, fixture_entry

def ok(command, **kwargs):
    stdout = '{"marketplaces":[]}' if command[1:] == ["plugin","marketplace","list","--json"] else ""
    return subprocess.CompletedProcess(command, 0, stdout, "")

def test_clean_repeat_refresh_and_new_version(tmp_path):
    data=archive_bytes(); entry=fixture_entry(data); calls=[]
    def runner(command, **kwargs): calls.append(command); return ok(command)
    first=install(entry, root=tmp_path, downloader=downloader(data), codex="/fake/codex", runner=runner)
    assert first.outcome == "installed"
    assert calls == [["/fake/codex","plugin","marketplace","list","--json"],["/fake/codex","plugin","marketplace","add",str(first.marketplace_root.resolve())],["/fake/codex","plugin","add","lgtm@sparepartslabs"]]
    assert install(entry, root=tmp_path, downloader=downloader(data), codex="codex", runner=ok).outcome == "unchanged"
    assert install(entry, refresh=True, root=tmp_path, downloader=downloader(data), codex="codex", runner=ok).outcome == "refreshed"
    newer=archive_bytes(root="marketplace-1.2.4",version="1.2.4"); newer_entry=fixture_entry(newer,root="marketplace-1.2.4",version="1.2.4")
    assert install(newer_entry, root=tmp_path, downloader=downloader(newer), codex="codex", runner=ok).outcome == "refreshed"

def test_digest_failure_preserves_receipt(tmp_path):
    data=archive_bytes(); entry=fixture_entry(data); install(entry,root=tmp_path,downloader=downloader(data),codex="codex",runner=ok)
    receipt=tmp_path/"receipts/lgtm.json"; before=receipt.read_bytes()
    with pytest.raises(InstallError, match="integrity"):
        install(entry,root=tmp_path,downloader=downloader(b"wrong"),codex="codex",runner=ok)
    assert receipt.read_bytes() == before

@pytest.mark.parametrize("name", ["../escape", "/absolute", "root/link"])
def test_rejects_unsafe_archive_members(tmp_path, name):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode="w:gz") as archive:
        info=tarfile.TarInfo(name)
        if name.endswith("link"): info.type=tarfile.SYMTYPE; info.linkname="target"
        archive.addfile(info)
    data=output.getvalue(); entry=fixture_entry(data,root="root")
    with pytest.raises(InstallError, match="archive"):
        install(entry,root=tmp_path,downloader=downloader(data),codex="codex",runner=ok)

@pytest.mark.parametrize("kind", [tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE])
def test_rejects_other_special_archive_members(tmp_path, kind):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode="w:gz") as archive:
        info=tarfile.TarInfo("root/special"); info.type=kind; info.linkname="root/target"; archive.addfile(info)
    data=output.getvalue()
    with pytest.raises(InstallError, match="unsupported member type"):
        install(fixture_entry(data,root="root"),root=tmp_path,downloader=downloader(data),codex="codex",runner=ok)

def test_rejects_member_count_and_extracted_size(tmp_path, monkeypatch):
    data=archive_bytes(); entry=fixture_entry(data)
    monkeypatch.setattr(installer,"MAX_MEMBERS",1)
    with pytest.raises(InstallError, match="members"):
        install(entry,root=tmp_path,downloader=downloader(data),codex="codex",runner=ok)
    monkeypatch.setattr(installer,"MAX_MEMBERS",512); monkeypatch.setattr(installer,"MAX_EXTRACTED_BYTES",1)
    with pytest.raises(InstallError, match="size limit"):
        install(entry,root=tmp_path,downloader=downloader(data),codex="codex",runner=ok)

def test_rejects_missing_canonical_command(tmp_path):
    data=archive_bytes(); output=io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(data),mode="r:gz") as source, tarfile.open(fileobj=output,mode="w:gz") as target:
        for member in source.getmembers():
            if member.name.endswith("/commands/lgtm.md"): continue
            target.addfile(member, source.extractfile(member) if member.isfile() else None)
    missing=output.getvalue()
    with pytest.raises(InstallError,match="canonical LGTM command"):
        install(fixture_entry(missing),root=tmp_path,downloader=downloader(missing),codex="codex",runner=ok)

def test_cache_permission_failure_is_typed(tmp_path, monkeypatch):
    data=archive_bytes(); entry=fixture_entry(data)
    monkeypatch.setattr(installer.tempfile,"TemporaryDirectory",lambda **_kwargs: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(InstallError, match="cache: denied"):
        install(entry,root=tmp_path,downloader=downloader(data),codex="codex",runner=ok)

def test_missing_codex_precedes_download(tmp_path, monkeypatch):
    data=archive_bytes(); entry=fixture_entry(data)
    monkeypatch.setattr(installer.shutil, "which", lambda _name: None)
    with pytest.raises(InstallError, match="Codex CLI was not found"):
        install(entry,root=tmp_path,downloader=lambda *_: pytest.fail("downloaded"),codex=None)

def test_download_failure_has_download_phase(tmp_path):
    data=archive_bytes(); entry=fixture_entry(data)
    def fail(*_args): raise OSError("offline")
    with pytest.raises(InstallError, match="download: offline"):
        install(entry,root=tmp_path,downloader=fail,codex="codex",runner=ok)

@pytest.mark.parametrize(("failure","phase"), [(1,"marketplace registration"),(2,"plugin installation")])
def test_codex_phase_errors_preserve_prior_receipt(tmp_path, failure, phase):
    data=archive_bytes(); entry=fixture_entry(data); install(entry,root=tmp_path,downloader=downloader(data),codex="codex",runner=ok)
    receipt=tmp_path/"receipts/lgtm.json"; before=receipt.read_bytes(); count=0
    def runner(command, **kwargs):
        nonlocal count
        current=count; count += 1
        return subprocess.CompletedProcess(command, 7, "", "boom") if current == failure else ok(command)
    with pytest.raises(InstallError, match=phase): install(entry,root=tmp_path,downloader=downloader(data),codex="codex",runner=runner)
    assert receipt.read_bytes() == before

def test_foreign_receipt_refused_and_unrelated_files_preserved(tmp_path):
    receipt=tmp_path/"receipts/lgtm.json"; receipt.parent.mkdir(parents=True); receipt.write_text('{"foreign":true}')
    unrelated=tmp_path/"preferences.json"; unrelated.write_text("keep")
    data=archive_bytes()
    with pytest.raises(InstallError, match="foreign"):
        install(fixture_entry(data),root=tmp_path,downloader=downloader(data),codex="codex",runner=ok)
    assert unrelated.read_text() == "keep"

def marketplace_runner(root=None, *, failures=None):
    calls=[]; failures=failures or {}
    def run(command, **kwargs):
        calls.append(command); index=len(calls)-1
        if index in failures: return subprocess.CompletedProcess(command,7,"",failures[index])
        stdout=json.dumps({"marketplaces":[] if root is None else [{"name":"sparepartslabs","root":str(root)}]}) if command[2:5] == ["marketplace","list","--json"] else ""
        return subprocess.CompletedProcess(command,0,stdout,"")
    return calls, run

def test_marketplace_absent_is_added(tmp_path):
    data=archive_bytes(); calls,runner=marketplace_runner()
    result=install(fixture_entry(data),root=tmp_path,downloader=downloader(data),codex="codex",runner=runner)
    assert [call[2] for call in calls] == ["marketplace","marketplace","add"]
    assert calls[1][-1] == str(result.marketplace_root.resolve())

def test_owned_same_marketplace_skips_add(tmp_path):
    data=archive_bytes(); entry=fixture_entry(data); first=install(entry,root=tmp_path,downloader=downloader(data),codex="codex",runner=ok)
    calls,runner=marketplace_runner(first.marketplace_root)
    install(entry,root=tmp_path,downloader=downloader(data),codex="codex",runner=runner)
    assert calls == [["codex","plugin","marketplace","list","--json"],["codex","plugin","add","lgtm@sparepartslabs"]]

def test_foreign_same_name_marketplace_is_refused(tmp_path):
    data=archive_bytes(); calls,runner=marketplace_runner(tmp_path/"foreign")
    with pytest.raises(InstallError,match="foreign root"):
        install(fixture_entry(data),root=tmp_path,downloader=downloader(data),codex="codex",runner=runner)
    assert len(calls) == 1

def test_unowned_matching_root_is_refused(tmp_path):
    data=archive_bytes(); entry=fixture_entry(data)
    expected=tmp_path/"versions"/f"sparepartslabs-{entry.version}-{entry.sha256[:12]}"
    calls,runner=marketplace_runner(expected)
    with pytest.raises(InstallError,match="unowned root"):
        install(entry,root=tmp_path,downloader=downloader(data),codex="codex",runner=runner)
    assert len(calls) == 1

def test_owned_upgrade_removes_then_adds(tmp_path):
    old=archive_bytes(); old_entry=fixture_entry(old); prior=install(old_entry,root=tmp_path,downloader=downloader(old),codex="codex",runner=ok)
    new=archive_bytes(root="marketplace-1.2.4",version="1.2.4"); entry=fixture_entry(new,root="marketplace-1.2.4",version="1.2.4")
    calls,runner=marketplace_runner(prior.marketplace_root)
    result=install(entry,root=tmp_path,downloader=downloader(new),codex="codex",runner=runner)
    assert calls[1] == ["codex","plugin","marketplace","remove","sparepartslabs"]
    assert calls[2] == ["codex","plugin","marketplace","add",str(result.marketplace_root.resolve())]

def test_owned_upgrade_rolls_back_when_add_fails(tmp_path):
    old=archive_bytes(); old_entry=fixture_entry(old); prior=install(old_entry,root=tmp_path,downloader=downloader(old),codex="codex",runner=ok)
    before=(tmp_path/"receipts/lgtm.json").read_bytes()
    new=archive_bytes(root="marketplace-1.2.4",version="1.2.4"); entry=fixture_entry(new,root="marketplace-1.2.4",version="1.2.4")
    calls,runner=marketplace_runner(prior.marketplace_root,failures={2:"add failed"})
    with pytest.raises(InstallError,match="restored prior root"):
        install(entry,root=tmp_path,downloader=downloader(new),codex="codex",runner=runner)
    assert calls[3] == ["codex","plugin","marketplace","add",str(prior.marketplace_root.resolve())]
    assert (tmp_path/"receipts/lgtm.json").read_bytes() == before

def test_owned_upgrade_reports_failed_rollback(tmp_path):
    old=archive_bytes(); old_entry=fixture_entry(old); prior=install(old_entry,root=tmp_path,downloader=downloader(old),codex="codex",runner=ok)
    new=archive_bytes(root="marketplace-1.2.4",version="1.2.4"); entry=fixture_entry(new,root="marketplace-1.2.4",version="1.2.4")
    _calls,runner=marketplace_runner(prior.marketplace_root,failures={2:"add failed",3:"rollback failed"})
    with pytest.raises(InstallError,match="rollback also failed"):
        install(entry,root=tmp_path,downloader=downloader(new),codex="codex",runner=runner)
