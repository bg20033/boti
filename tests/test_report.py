import time

from boti.cli import report
from boti.db import encode, uid


def test_two_day_total_is_not_misreported_as_daily_capacity(store):
    settings = store.settings()
    settings.target_clips_daily = 1
    store.save_settings(settings)
    job_id, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute(
        """INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,status,created,updated)
                     VALUES (?,?,?,0,20,90,?,'ready',?,?)""",
        (uid(), job_id, "pc-1-g1", encode({"title": "Test"}), time.time(), time.time()),
    )
    result = report(store, since=time.time() - 48 * 3600)
    assert result["completed_clips"] == 1
    assert result["generation_target_met"] is False
    assert result["pilot_passed"] is False
    assert result["target_confirmed"] is False
