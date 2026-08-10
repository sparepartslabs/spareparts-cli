import json
import pytest
from spareparts.modules.plugin.catalog import CatalogError, load_catalog

BASE = {"name":"lgtm","marketplace":"sparepartslabs","version":"1.2.3","archive_url":"https://example.test/a.tar.gz","sha256":"a"*64,"archive_root":"root","install_identity":"lgtm@sparepartslabs"}

def load(tmp_path, entry):
    path=tmp_path/"catalog.json"; path.write_text(json.dumps({"plugins":[entry]})); return load_catalog(path)

def test_valid_catalog(tmp_path): assert load(tmp_path, BASE)[0].install_identity == "lgtm@sparepartslabs"

@pytest.mark.parametrize(("key","value"), [("name","other"),("marketplace","other"),("version","v1"),("archive_url","http://bad"),("sha256","A"*64),("archive_root","../bad"),("install_identity","lgtm")])
def test_rejects_invalid_contract_fields(tmp_path, key, value):
    with pytest.raises(CatalogError): load(tmp_path, {**BASE,key:value})

def test_rejects_unknown_keys(tmp_path):
    with pytest.raises(CatalogError): load(tmp_path, {**BASE,"extra":True})
