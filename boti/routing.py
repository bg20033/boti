"""Route editorial work to the main PC and execution to connected workers."""

import json
import time

EDITORIAL = frozenset({"analyze", "quality"})
EXECUTION = frozenset({"transcribe", "render", "repair"})


def eligible_capabilities(workers, primary_id=None, now=None, render_revision=0):
    now = time.time() if now is None else now
    available = {}
    for worker in workers:
        capabilities = worker["capabilities"]
        if isinstance(capabilities, str):
            capabilities = json.loads(capabilities)
        available[worker["id"]] = (
            set(capabilities) if not worker["paused"] and (worker["last_seen"] or 0) > now - 30 else set()
        )
        if render_revision:
            hardware = worker["hardware"]
            if isinstance(hardware, str):
                hardware = json.loads(hardware)
            if hardware.get("render_revision", 0) < render_revision:
                available[worker["id"]].discard("render")
    if not primary_id:
        return available
    primary = available.get(primary_id, set())
    for kind in EDITORIAL & primary:
        for identity, capabilities in available.items():
            if identity != primary_id:
                capabilities.discard(kind)
    # Keep execution on remote PCs, even while they are busy. When no capable
    # remote remains connected, the main can continue the queue by itself.
    for kind in EXECUTION:
        if any(kind in caps for identity, caps in available.items() if identity != primary_id):
            primary.discard(kind)
    return available
