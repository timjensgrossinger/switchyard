#!/usr/bin/env python3
"""
Provider health state machine and circuit-breaker helpers.

State transitions:
  HEALTHY     → DEGRADED    (first failure)
  DEGRADED    → QUARANTINED (consecutive_failures >= threshold)
  QUARANTINED → PROBING     (cooldown elapsed; bg probe selects it)
  PROBING     → HEALTHY     (probe succeeded)
  PROBING     → QUARANTINED (probe failed; exponential cooldown)
"""
from __future__ import annotations

import logging
import time
import typing

if typing.TYPE_CHECKING:
    from .db import Database

log = logging.getLogger(__name__)

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
QUARANTINED = "QUARANTINED"
PROBING = "PROBING"

_COOLDOWN: dict[str, float] = {
    "auth_expired":   600.0,
    "quota_exceeded": 1800.0,
    "binary_missing": 86400.0,
    "zombie_reaped":  60.0,
    "default":        120.0,
}
_MAX_COOLDOWN = 3600.0
_FAILURE_THRESHOLD = 3


def is_available(db: "Database", provider_id: str) -> bool:
    """Return True if the provider is eligible for routing.

    If QUARANTINED and the cooldown has elapsed, transitions to PROBING and
    returns True so one probe attempt can pass through.
    """
    row = db.get_provider_health(provider_id)
    if row is None:
        return True

    state = row.get("state", HEALTHY)
    if state in (HEALTHY, DEGRADED, PROBING):
        return True

    if state == QUARANTINED:
        until = row.get("quarantine_until_ts")
        if until is not None and time.time() >= until:
            db.update_provider_health_state(provider_id, PROBING)
            return True
        return False

    return True


def record_provider_failure(
    db: "Database",
    provider_id: str,
    category: str,
    stderr: str = "",
    exit_code: int | None = None,
) -> None:
    """Increment failure counter and transition state accordingly."""
    row = db.get_provider_health(provider_id)
    current_failures = row["consecutive_failures"] if row else 0
    new_failures = current_failures + 1
    now = time.time()
    stderr_snippet = (stderr or "")[:2000] or None

    terminal = category in ("auth_expired", "binary_missing", "quota_exceeded")
    if new_failures >= _FAILURE_THRESHOLD or terminal:
        new_state = QUARANTINED
        base_cooldown = _COOLDOWN.get(category, 120.0)
        quarantine_count = 0
        if row and row.get("state") == QUARANTINED:
            quarantine_count = max(1, new_failures - _FAILURE_THRESHOLD)
        cooldown = (
            min(base_cooldown * (2 ** quarantine_count), _MAX_COOLDOWN)
            if quarantine_count > 0
            else base_cooldown
        )
        quarantine_until: float | None = now + cooldown
    else:
        new_state = DEGRADED
        quarantine_until = None

    db.update_provider_health_state(
        provider_id,
        new_state,
        consecutive_failures=new_failures,
        last_failure_ts=now,
        last_failure_category=category,
        last_failure_stderr=stderr_snippet,
        quarantine_until_ts=quarantine_until,
    )
    log.debug(
        "provider %s: failure #%d category=%s → %s",
        provider_id, new_failures, category, new_state,
    )


def record_provider_success(db: "Database", provider_id: str) -> None:
    """Reset failure counter and transition provider to HEALTHY."""
    db.update_provider_health_state(
        provider_id,
        HEALTHY,
        consecutive_failures=0,
        last_probe_ok=True,
        last_probe_ts=time.time(),
    )
    log.debug("provider %s: success → HEALTHY", provider_id)


def record_probe_result(db: "Database", provider_id: str, ok: bool) -> None:
    """Record background probe result (PROBING → HEALTHY or QUARANTINED)."""
    now = time.time()
    if ok:
        db.update_provider_health_state(
            provider_id,
            HEALTHY,
            consecutive_failures=0,
            last_probe_ts=now,
            last_probe_ok=True,
        )
        log.info("provider %s: probe succeeded → HEALTHY", provider_id)
    else:
        row = db.get_provider_health(provider_id)
        current_failures = row["consecutive_failures"] if row else _FAILURE_THRESHOLD
        category = (row.get("last_failure_category") or "default") if row else "default"
        base_cooldown = _COOLDOWN.get(category, 120.0)
        quarantine_count = max(1, current_failures - _FAILURE_THRESHOLD + 1)
        cooldown = min(base_cooldown * (2 ** quarantine_count), _MAX_COOLDOWN)
        db.update_provider_health_state(
            provider_id,
            QUARANTINED,
            last_probe_ts=now,
            last_probe_ok=False,
            quarantine_until_ts=now + cooldown,
        )
        log.warning(
            "provider %s: probe failed → QUARANTINED for %.0fs",
            provider_id, cooldown,
        )
