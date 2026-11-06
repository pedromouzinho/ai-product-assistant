"""In-memory agent request metrics accumulator."""

from __future__ import annotations

import threading
from collections import defaultdict
from collections import deque
from datetime import datetime, timezone
from typing import Any


class AgentMetrics:
    """Thread-safe accumulator for high-level agent request metrics."""

    def __init__(self, max_latencies: int = 500, max_recent_events: int = 200):
        self._lock = threading.Lock()
        self._max_latencies = max_latencies
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=max_recent_events)
        self._overall: dict[str, dict[str, Any]] = {
            "request_count": 0,
            "error_count": 0,
            "blocked_count": 0,
            "fallback_count": 0,
            "total_tools": 0,
            "total_ms": 0,
            "latencies": [],
            "error_kind_counts": defaultdict(int),
        }
        self._routes: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "request_count": 0,
                "error_count": 0,
                "blocked_count": 0,
                "fallback_count": 0,
                "total_tools": 0,
                "total_ms": 0,
                "latencies": [],
                "error_kind_counts": defaultdict(int),
            }
        )

    def _record_bucket(
        self,
        bucket: dict[str, Any],
        *,
        duration_ms: int,
        tool_count: int,
        fallback: bool,
        status: str,
        error_kind: str,
    ) -> None:
        bucket["request_count"] += 1
        bucket["total_ms"] += max(0, duration_ms)
        bucket["total_tools"] += max(0, tool_count)
        if fallback:
            bucket["fallback_count"] += 1
        if status == "error":
            bucket["error_count"] += 1
        elif status == "blocked":
            bucket["blocked_count"] += 1
        safe_error_kind = str(error_kind or "").strip().lower()
        if safe_error_kind:
            bucket["error_kind_counts"][safe_error_kind] += 1
        lats = bucket["latencies"]
        lats.append(max(0, duration_ms))
        if len(lats) > self._max_latencies:
            bucket["latencies"] = lats[-self._max_latencies :]

    def record(
        self,
        route_name: str,
        duration_ms: int,
        *,
        tool_count: int = 0,
        fallback: bool = False,
        status: str = "ok",
        request_id: str = "",
        conversation_id: str = "",
        model_used: str = "",
        error_kind: str = "",
    ) -> None:
        safe_route = str(route_name or "").strip() or "unknown"
        with self._lock:
            self._record_bucket(
                self._overall,
                duration_ms=duration_ms,
                tool_count=tool_count,
                fallback=fallback,
                status=status,
                error_kind=error_kind,
            )
            self._recent_events.append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "route": safe_route,
                    "duration_ms": max(0, duration_ms),
                    "tool_count": max(0, tool_count),
                    "fallback": bool(fallback),
                    "status": str(status or "").strip() or "ok",
                    "request_id": str(request_id or "").strip(),
                    "conversation_id": str(conversation_id or "").strip(),
                    "model_used": str(model_used or "").strip(),
                    "error_kind": str(error_kind or "").strip().lower(),
                }
            )
            self._record_bucket(
                self._routes[safe_route],
                duration_ms=duration_ms,
                tool_count=tool_count,
                fallback=fallback,
                status=status,
                error_kind=error_kind,
            )

    @staticmethod
    def _summarize_bucket(entry: dict[str, Any]) -> dict[str, Any]:
        lats = sorted(entry["latencies"]) if entry["latencies"] else []
        n = len(lats)
        request_count = entry["request_count"]
        fallback_count = entry["fallback_count"]
        return {
            "request_count": request_count,
            "error_count": entry["error_count"],
            "blocked_count": entry["blocked_count"],
            "fallback_count": fallback_count,
            "fallback_rate": round(fallback_count / request_count, 4) if request_count else 0.0,
            "total_tools": entry["total_tools"],
            "avg_tools_per_request": round(entry["total_tools"] / request_count, 2) if request_count else 0.0,
            "avg_ms": round(entry["total_ms"] / max(1, request_count)),
            "p50_ms": lats[n // 2] if n else 0,
            "p95_ms": lats[int(n * 0.95)] if n else 0,
            "p99_ms": lats[int(n * 0.99)] if n else 0,
            "max_ms": lats[-1] if n else 0,
            "recent_samples": n,
            "error_kind_counts": dict(sorted(entry["error_kind_counts"].items())),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "overall": self._summarize_bucket(self._overall),
                "by_route": {
                    route_name: self._summarize_bucket(entry)
                    for route_name, entry in sorted(self._routes.items())
                },
            }

    def recent_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._recent_events)

    def reset(self) -> None:
        with self._lock:
            self._overall = {
                "request_count": 0,
                "error_count": 0,
                "blocked_count": 0,
                "fallback_count": 0,
                "total_tools": 0,
                "total_ms": 0,
                "latencies": [],
                "error_kind_counts": defaultdict(int),
            }
            self._routes.clear()
            self._recent_events.clear()


agent_metrics = AgentMetrics()
