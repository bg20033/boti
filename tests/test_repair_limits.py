import pytest


@pytest.mark.parametrize(
    "kind,delta,status", [("boundaries", 5, 200), ("boundaries", 7, 422), ("captions", 5, 422)]
)
def test_sentence_repair_can_finish_six_seconds_later_but_other_recipes_cannot_move_cuts(
    store, client, worker_headers, kind, delta, status
):
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET status='processing' WHERE id=?", (job,))
    metadata = {"start": 0, "end": 25, "score": 90, "title": "A complete moment", "source_duration": 100}
    store.add_task(
        job,
        "repair",
        "repair",
        {"metadata": metadata, "limits": {"min_seconds": 20, "max_seconds": 60}, "repair": kind},
    )
    store.heartbeat("pc-1", ["repair"], {})
    task = store.claim("pc-1")
    assert task
    response = client.post(
        f"/api/worker/tasks/{task['id']}/complete",
        headers=worker_headers["pc-1"],
        json={
            "lease_token": task["lease_token"],
            "result": {"metadata": {**metadata, "end": 25 + delta, "segments": []}},
        },
    )
    assert response.status_code == status
