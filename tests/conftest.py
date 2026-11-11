import json

import pytest
from fastapi.testclient import TestClient

from boti.config import Config
from boti.db import Store
from boti.server import create_app


@pytest.fixture
def store(tmp_path):
    value = Store(Config(tmp_path / "data"))
    value.seed()
    settings = value.settings()
    settings.min_free_gb = 0
    value.save_settings(settings)
    return value


@pytest.fixture
def client(store):
    with TestClient(create_app(store.config.root, background=False)) as value:
        yield value


@pytest.fixture
def admin_headers(store):
    token = (store.config.root / "secrets" / "admin.token").read_text()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def worker_headers(store):
    result = {}
    for index in range(1, 5):
        config = json.loads((store.config.root / "secrets" / f"worker-{index}.json").read_text())
        result[config["id"]] = {"Authorization": f"Bearer {config['token']}"}
        store.heartbeat(config["id"], ["transcribe", "analyze", "render"], {"test": True})
    return result
