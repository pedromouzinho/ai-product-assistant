from __future__ import annotations

from types import SimpleNamespace

from starlette.requests import Request

import route_deps


def _make_request(host: str, origin: str, scheme: str = "https") -> Request:
    headers = [
        (b"host", host.encode("utf-8")),
        (b"origin", origin.encode("utf-8")),
        (b"x-forwarded-proto", scheme.encode("utf-8")),
    ]
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": scheme,
        "path": "/api/auth/login",
        "headers": headers,
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
        "server": (host, 443 if scheme == "https" else 80),
    }
    return Request(scope)


def test_origin_allowlist_accepts_same_origin_custom_domain(monkeypatch):
    monkeypatch.setattr(route_deps, "_allowed_origins_set", {"https://app.example.com"})
    request = _make_request("assistant.example.com", "https://assistant.example.com")

    assert route_deps._origin_allowed_for_request(request, "https://assistant.example.com") is True


def test_origin_allowlist_rejects_unknown_cross_origin(monkeypatch):
    monkeypatch.setattr(route_deps, "_allowed_origins_set", {"https://app.example.com"})
    request = _make_request("assistant.example.com", "https://evil.example")

    assert route_deps._origin_allowed_for_request(request, "https://evil.example") is False


def test_origin_allowlist_still_accepts_configured_origin(monkeypatch):
    monkeypatch.setattr(
        route_deps,
        "_allowed_origins_set",
        {"https://app.example.com", "https://assistant.example.com"},
    )
    request = _make_request(
        "app.example.com",
        "https://assistant.example.com",
    )

    assert route_deps._origin_allowed_for_request(request, "https://assistant.example.com") is True


def test_resolve_rate_limit_rule_returns_none_when_route_limits_disabled(monkeypatch):
    @route_deps.limiter.limit("5/minute", key_func=route_deps._user_or_ip_rate_key)
    def _endpoint():
        return "ok"

    request = _make_request("assistant.example.com", "https://assistant.example.com")
    request.scope["route"] = SimpleNamespace(endpoint=_endpoint, path="/chat/agent")
    monkeypatch.setattr(route_deps, "ROUTE_RATE_LIMIT_ENFORCEMENT_ENABLED", False)

    assert route_deps.resolve_rate_limit_rule(request) is None


def test_resolve_rate_limit_rule_returns_rule_when_route_limits_enabled(monkeypatch):
    @route_deps.limiter.limit("5/minute", key_func=route_deps._user_or_ip_rate_key)
    def _endpoint():
        return "ok"

    request = _make_request("assistant.example.com", "https://assistant.example.com")
    request.scope["route"] = SimpleNamespace(endpoint=_endpoint, path="/chat/agent")
    monkeypatch.setattr(route_deps, "ROUTE_RATE_LIMIT_ENFORCEMENT_ENABLED", True)

    rule = route_deps.resolve_rate_limit_rule(request)

    assert rule is not None
    assert rule.max_requests == 5
    assert rule.window_seconds == 60
