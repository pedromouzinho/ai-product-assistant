import base64
from pathlib import Path

import app
from code_interpreter import (
    _build_subprocess_env,
    _is_safe_symlink_source,
    _set_resource_limits,
    _validate_code,
)
from http_helpers import _sanitize_error_response


class TestSanitizeErrorResponse:
    """Verify that secrets are redacted from logged error responses."""

    def test_redacts_api_key_in_response(self):
        fake_key = "-".join(["sk", "abc123def456"])  # synthetic, assembled at runtime
        text = '{"error": "Invalid api-key: ' + fake_key + '"}'
        result = _sanitize_error_response(text, 200)

        assert fake_key not in result
        assert "[REDACTED]" in result

    def test_redacts_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.xxx"
        result = _sanitize_error_response(text, 300)

        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "[REDACTED]" in result

    def test_redacts_base64_token(self):
        token = base64.b64encode(b":my-secret-pat-token-value-here").decode("ascii")
        result = _sanitize_error_response(f"Basic {token}", 200)

        assert token not in result
        assert "[REDACTED]" in result

    def test_preserves_status_code(self):
        text = "HTTP 401 Unauthorized: invalid credentials"
        result = _sanitize_error_response(text, 200)

        assert "401" in result
        assert "Unauthorized" in result

    def test_truncates_to_max_len(self):
        result = _sanitize_error_response("x" * 500, 200)

        assert len(result) <= 200

    def test_empty_input(self):
        assert _sanitize_error_response("", 200) == ""
        assert _sanitize_error_response(None, 200) == ""


class TestCodeInterpreterHardening:
    """Verify the sandbox hardening for the code interpreter."""

    def test_path_is_minimal(self, tmp_path):
        env = _build_subprocess_env(str(tmp_path))

        assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"
        assert "/sbin" not in env["PATH"]

    def test_blocked_from_os_import_system(self):
        err = _validate_code("from os import system")

        assert err is not None
        assert "bloquead" in err.lower()

    def test_blocked_from_pandas_import_eval(self):
        err = _validate_code("from pandas import eval")

        assert err is not None
        assert "bloquead" in err.lower()

    def test_blocked_getattr_builtins(self):
        err = _validate_code("getattr(__builtins__, 'exec')()")

        assert err is not None

    def test_allowed_pandas_import(self):
        assert _validate_code("import pandas as pd") is None

    def test_allowed_from_pandas_import(self):
        assert _validate_code("from pandas import DataFrame") is None

    def test_hasattr_remains_allowed(self):
        assert _validate_code("print(hasattr(object(), '__class__'))") is None

    def test_symlink_validation_blocks_escape(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        escape_link = sandbox / "escape.txt"
        escape_link.symlink_to(outside)

        assert not _is_safe_symlink_source(str(escape_link), str(sandbox))

    def test_symlink_validation_allows_internal_target(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        inside = sandbox / "inside.txt"
        inside.write_text("ok", encoding="utf-8")
        safe_link = sandbox / "safe.txt"
        safe_link.symlink_to(inside)

        assert _is_safe_symlink_source(str(safe_link), str(sandbox))

    def test_resource_limits_function_exists(self):
        assert callable(_set_resource_limits)

    def test_runner_env_isolated_from_host_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", "/tmp/evil:/usr/sbin:/usr/bin")

        env = _build_subprocess_env(str(tmp_path))

        assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"
        assert "/tmp/evil" not in env["PATH"]
        assert "/usr/sbin" not in env["PATH"]


class TestSecurityHeaders:
    def test_permissions_policy_allows_microphone_for_self(self):
        assert app._build_permissions_policy() == "camera=(), microphone=(self), geolocation=()"

    def test_csp_includes_speech_endpoints_when_enabled(self, monkeypatch):
        monkeypatch.setattr(app, "AZURE_SPEECH_ENABLED", True)
        monkeypatch.setattr(app, "AZURE_SPEECH_REGION", "swedencentral")

        csp = app._build_content_security_policy()

        assert "connect-src 'self'" in csp
        assert "https://swedencentral.stt.speech.microsoft.com" in csp
        assert "wss://swedencentral.stt.speech.microsoft.com" in csp
        assert "https://*.speech.microsoft.com" in csp
        assert "wss://*.speech.microsoft.com" in csp

    def test_csp_stays_local_when_speech_disabled(self, monkeypatch):
        monkeypatch.setattr(app, "AZURE_SPEECH_ENABLED", False)
        monkeypatch.setattr(app, "AZURE_SPEECH_REGION", "")

        csp = app._build_content_security_policy()

        assert csp.endswith("connect-src 'self';")
