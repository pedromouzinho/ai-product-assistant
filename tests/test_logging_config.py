from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_config_defines_log_level():
    config_text = _read("config.py")
    assert 'LOG_LEVEL = _get_env("LOG_LEVEL", "INFO").upper()' in config_text


def test_app_quiets_noisy_http_and_telemetry_loggers():
    app_text = _read("app.py")
    assert "NOISY_PROD_LOGGERS" in app_text
    assert '"httpx"' in app_text
    assert '"httpcore"' in app_text
    assert '"azure.core.pipeline.policies.http_logging_policy"' in app_text
    assert '"azure.monitor.opentelemetry.exporter.export._base"' in app_text
    assert "logging.getLogger(logger_name).setLevel(logging.WARNING)" in app_text
    assert "configure_logging(LOG_FORMAT, LOG_LEVEL)" in app_text


def test_workers_apply_same_log_quieting_policy():
    upload_text = _read("upload_worker.py")
    export_text = _read("export_worker.py")
    for text in (upload_text, export_text):
        assert "NOISY_WORKER_LOGGERS" in text
        assert '"httpx"' in text
        assert '"httpcore"' in text
        assert '"azure.core.pipeline.policies.http_logging_policy"' in text
        assert '"azure.monitor.opentelemetry.exporter.export._base"' in text
        assert "logging.getLogger().setLevel(_resolve_log_level(LOG_LEVEL))" in text
