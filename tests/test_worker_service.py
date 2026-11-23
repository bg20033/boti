import pytest

from scripts.install_worker_service import service_text, unit_quote


def test_worker_service_quotes_paths_and_restarts():
    text = service_text(
        "/opt/Boti Project/.venv/bin/python", "/opt/Boti Project", "/opt/worker%.json", "/usr/bin"
    )
    assert 'WorkingDirectory="/opt/Boti Project"' in text
    assert '--config "/opt/worker%%.json"' in text
    assert "Restart=always" in text
    assert "StartLimitIntervalSec=0" in text
    assert "WantedBy=default.target" in text


def test_worker_service_rejects_multiline_paths():
    with pytest.raises(ValueError):
        unit_quote("/opt/config\nRestart=no")


def test_dollar_is_literal_in_service_paths():
    assert unit_quote("/opt/$worker") == '"/opt/$worker"'
    assert unit_quote("/opt/$worker", exec_argument=True) == '"/opt/$$worker"'
