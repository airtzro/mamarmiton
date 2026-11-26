import pytest

from mamarmiton import config


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Every test gets its own empty data folder and only sees ``tmp_path/img``."""
    data = tmp_path / "data"
    monkeypatch.setenv("MAMARMITON_DATA_DIR", str(data))
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / "img"
    monkeypatch.setattr(config, "legacy_image_dirs", lambda: [legacy] if legacy.is_dir() else [])
    return data
