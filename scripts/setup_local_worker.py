"""Give the main PC its own worker identity, separate from the four remote PCs."""

import secrets
from pathlib import Path

from boti.config import Config, private_write
from boti.db import Store, token_hash
from boti.worker import WorkerConfig

root = Path(__file__).resolve().parents[1]
store = Store(Config(root / "data"))
path = store.config.root / "secrets" / "worker-local.json"
identity = "main-local"
existing = store.one("SELECT token_hash FROM workers WHERE id=?", (identity,))
if existing:
    config = WorkerConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    if config.id != identity or token_hash(config.token) != existing["token_hash"]:
        raise RuntimeError("The existing main-local identity needs its original private configuration")
else:
    config = WorkerConfig(
        id=identity,
        token=secrets.token_urlsafe(32),
        coordinator_url="http://127.0.0.1:8765",
    )
    private_write(path, config.model_dump_json(indent=2))
    store.execute(
        "INSERT INTO workers(id,name,token_hash) VALUES (?,?,?)",
        (identity, "Main - worker lokal", token_hash(config.token)),
    )
print(f"Local worker ready: {identity}")
