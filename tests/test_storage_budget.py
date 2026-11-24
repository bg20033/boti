from boti.storage_budget import ingest_reserve, source_bytes


def test_source_budget_prefers_actual_size_and_rejects_unknown_or_invalid_bitrates():
    assert source_bytes({"filesize": 100, "tbr": 800, "duration": 10}) == 100
    assert source_bytes({"tbr": 800, "duration": 10}) == 1000000
    assert source_bytes({"tbr": float("nan"), "duration": 10}) == 0
    assert source_bytes({}) == 0


def test_download_budget_accounts_for_existing_resume_bytes_without_double_counting_fragments(tmp_path):
    (tmp_path / "source.mp4.part").write_bytes(b"x" * 100)
    (tmp_path / "source.mp4.part-Frag2.part").write_bytes(b"x" * 80)
    assert ingest_reserve({"download_estimated_bytes": 200, "reserve_bytes": 30}, tmp_path) == 130
    (tmp_path / "source.mp4.part").rename(tmp_path / "source.mp4")
    assert ingest_reserve({"download_estimated_bytes": 90, "reserve_bytes": 30}, tmp_path) == 30
