"""In-memory tool call metrics accumulator."""

from __future__ import annotations

import threading
from collections import defaultdict
from collections import deque
from datetime import datetime, timezone
from typing import Any


class ToolMetrics:
    """Thread-safe accumulator for tool call metrics."""

    def __init__(self, max_latencies: int = 500, max_recent_events: int = 200):
        self._lock = threading.Lock()
        self._max_latencies = max_latencies
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=max_recent_events)
        self._data: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "call_count": 0,
                "error_count": 0,
                "blocked_count": 0,
                "total_ms": 0,
                "latencies": [],
                "error_kind_counts": defaultdict(int),
            }
        )

    def record(
        self,
        tool_name: str,
        duration_ms: int,
        status: str = "ok",
        *,
        request_id: str = "",
        conversation_id: str = "",
        error_kind: str = "",
    ) -> None:
        with self._lock:
            entry = self._data[tool_name]
            entry["call_count"] += 1
            entry["total_ms"] += max(0, duration_ms)
            if status in ("exception", "tool_error"):
                entry["error_count"] += 1
            elif status == "blocked":
                entry["blocked_count"] += 1
            safe_error_kind = str(error_kind or "").strip().lower()
            if safe_error_kind:
                entry["error_kind_counts"][safe_error_kind] += 1
            lats = entry["latencies"]
            lats.append(max(0, duration_ms))
            if len(lats) > self._max_latencies:
                entry["latencies"] = lats[-self._max_latencies:]
            self._recent_events.append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "tool": str(tool_name or "").strip(),
                    "duration_ms": max(0, duration_ms),
                    "status": str(status or "").strip() or "ok",
                    "request_id": str(request_id or "").strip(),
                    "conversation_id": str(conversation_id or "").strip(),
                    "error_kind": str(error_kind or "").strip(),
                }
            )

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            result = {}
            for tool_name, entry in self._data.items():
                lats = sorted(entry["latencies"]) if entry["latencies"] else []
                n = len(lats)
                result[tool_name] = {
                    "call_count": entry["call_count"],
                    "error_count": entry["error_count"],
                    "blocked_count": entry["blocked_count"],
                    "avg_ms": round(entry["total_ms"] / max(1, entry["call_count"])),
                    "p50_ms": lats[n // 2] if n else 0,
                    "p95_ms": lats[int(n * 0.95)] if n else 0,
                    "p99_ms": lats[int(n * 0.99)] if n else 0,
                    "max_ms": lats[-1] if n else 0,
                    "recent_samples": n,
                    "error_kind_counts": dict(sorted(entry["error_kind_counts"].items())),
                }
            return dict(sorted(result.items()))

    def recent_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._recent_events)

    def reset(self) -> None:
        with self._lock:
            self._data.clear()
            self._recent_events.clear()


tool_metrics = ToolMetrics()
