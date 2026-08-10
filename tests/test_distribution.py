from __future__ import annotations

from pathlib import Path
import tarfile
import zipfile
import pytest


CATALOG_SUFFIX = "spareparts/modules/plugin/catalog.json"
EXPECTED_CATALOG = Path("src") / CATALOG_SUFFIX


def _artifact(pattern: str) -> Path:
    artifact = next(Path("dist").glob(pattern), None)
    if artifact is None:
        pytest.skip("distribution artifacts are validated after `python -m build`")
    return artifact


def test_built_wheel_contains_plugin_catalog():
    wheel = _artifact("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        member = next(name for name in archive.namelist() if name.endswith(CATALOG_SUFFIX))
        assert archive.read(member) == EXPECTED_CATALOG.read_bytes()


def test_built_sdist_contains_plugin_catalog():
    sdist = _artifact("*.tar.gz")
    with tarfile.open(sdist, "r:gz") as archive:
        member = next(name for name in archive.getnames() if name.endswith(CATALOG_SUFFIX))
        extracted = archive.extractfile(member)
        assert extracted is not None and extracted.read() == EXPECTED_CATALOG.read_bytes()
