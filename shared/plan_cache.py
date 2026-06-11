"""Plan cache operator metrics for inspect surfaces."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shared.db import Database

log = logging.getLogger(__name__)

PLAN_CACHE_HIT = "plan_cache_hit"
PLAN_CACHE_MISS = "plan_cache_miss"
PLAN_CACHE_EXPIRED = "plan_cache_expired"
PLAN_CACHE_SCHEMA_INVALID = "plan_cache_schema_invalid"

PLAN_CACHE_TELEMETRY_REASONS = (
    PLAN_CACHE_HIT,
    PLAN_CACHE_MISS,
    PLAN_CACHE_EXPIRED,
    PLAN_CACHE_SCHEMA_INVALID,
)

_DEFAULT_WINDOW_SECONDS = 7 * 86400


def estimated_planner_tokens_from_plan(plan: dict[str, Any]) -> int:
    """Return cached planner token estimate when present."""
    token_estimate = plan.get("token_estimate")
    if not isinstance(token_estimate, dict):
        return 0
    for key in ("planner_total", "estimated_total", "planner_total_tokens"):
        raw = token_estimate.get(key)
        if isinstance(raw, (int, float)) and raw >= 0:
            return int(raw)
    return 0


def build_plan_cache_summary(
    db: Database,
    *,
    since_ts: float | None = None,
    window_label: str = "7d",
) -> dict[str, Any]:
    """Aggregate plan cache table size and planner telemetry counters."""
    window_start = since_ts if since_ts is not None else time.time() - _DEFAULT_WINDOW_SECONDS
    summary: dict[str, Any] = {
        "window": window_label,
        "entries": 0,
        "hits": 0,
        "misses": 0,
        "expirations": 0,
        "schema_invalidations": 0,
        "estimated_planner_tokens_saved": 0,
    }
    try:
        with db.conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM plan_cache").fetchone()
            summary["entries"] = int(row[0] or 0) if row else 0
            rows = conn.execute(
                """
                SELECT reason, COUNT(*), COALESCE(SUM(estimated_tokens), 0)
                FROM telemetry
                WHERE version = 'planner'
                  AND ts >= ?
                  AND reason IN (?, ?, ?, ?)
                GROUP BY reason
                """,
                (
                    window_start,
                    PLAN_CACHE_HIT,
                    PLAN_CACHE_MISS,
                    PLAN_CACHE_EXPIRED,
                    PLAN_CACHE_SCHEMA_INVALID,
                ),
            ).fetchall()
    except Exception:
        log.debug("plan cache summary query failed", exc_info=True)
        return summary

    for reason, count, estimated_total in rows:
        count_int = int(count or 0)
        estimated_int = int(estimated_total or 0)
        if reason == PLAN_CACHE_HIT:
            summary["hits"] = count_int
            summary["estimated_planner_tokens_saved"] = estimated_int
        elif reason == PLAN_CACHE_MISS:
            summary["misses"] = count_int
        elif reason == PLAN_CACHE_EXPIRED:
            summary["expirations"] = count_int
        elif reason == PLAN_CACHE_SCHEMA_INVALID:
            summary["schema_invalidations"] = count_int

    hit_rate_denominator = summary["hits"] + summary["misses"]
    summary["hit_rate_pct"] = round(
        (summary["hits"] / max(hit_rate_denominator, 1)) * 100.0,
        1,
    )
    return summary


__all__ = [
    "PLAN_CACHE_EXPIRED",
    "PLAN_CACHE_HIT",
    "PLAN_CACHE_MISS",
    "PLAN_CACHE_SCHEMA_INVALID",
    "PLAN_CACHE_TELEMETRY_REASONS",
    "build_plan_cache_summary",
    "estimated_planner_tokens_from_plan",
]
