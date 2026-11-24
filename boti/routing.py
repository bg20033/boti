"""Route editorial work to the main PC and execution to connected workers."""

import json
import time

EDITORIAL = frozenset({"analyze", "quality"})
EXECUTION = frozenset({"transcribe", "render", "repair"})


def eligible_capabilities(
    workers, primary_id=None, now=None, render_revision=0, quality_worker_id=None, quality_parallel_workers=1
):
    now = time.time() if now is None else now
    available = {}
    hardware_by_id = {}
    for worker in workers:
        capabilities = worker["capabilities"]
        if isinstance(capabilities, str):
            capabilities = json.loads(capabilities)
        available[worker["id"]] = (
            set(capabilities) if not worker["paused"] and (worker["last_seen"] or 0) > now - 30 else set()
        )
        hardware = worker["hardware"]
        if isinstance(hardware, str):
            hardware = json.loads(hardware)
        hardware_by_id[worker["id"]] = hardware
        if render_revision:
            hardware = worker["hardware"]
            if isinstance(hardware, str):
                hardware = json.loads(hardware)
            if hardware.get("render_revision", 0) < render_revision:
                available[worker["id"]].discard("render")
    primary = available.get(primary_id, set())
    for kind in EDITORIAL & primary:
        if kind == "quality" and quality_worker_id:
            continue
        for identity, capabilities in available.items():
            if identity != primary_id:
                capabilities.discard(kind)
    if quality_worker_id:
        for identity, capabilities in available.items():
            if hardware_by_id[identity].get("quality_revision", 0) < 1:
                capabilities.discard("quality")
        preferred = available.get(quality_worker_id, set())
        target = quality_worker_id if "quality" in preferred else primary_id if "quality" in primary else None
        targets = {target} if target else set()
        if quality_parallel_workers > 1:
            remote = [
                identity
                for identity, caps in available.items()
                if identity != primary_id and "quality" in caps
            ]
            remote.sort(key=lambda identity: (identity != quality_worker_id, identity))
            if remote:
                targets = set(remote[:quality_parallel_workers])
        if targets:
            for identity, capabilities in available.items():
                if identity not in targets:
                    capabilities.discard("quality")
    # Keep execution on remote PCs, even while they are busy. When no capable
    # remote remains connected, the main can continue the queue by itself.
    for kind in EXECUTION:
        if any(kind in caps for identity, caps in available.items() if identity != primary_id):
            primary.discard(kind)
    return available
