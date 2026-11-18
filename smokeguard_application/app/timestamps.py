"""Timestamp sanity policy for live MQTT records.

The firmware stamps each CSI/smoke row with its SNTP-synced wall clock.  When
the receiver has no internet it cannot sync: ESP-IDF's ``gettimeofday()`` then
returns seconds since boot — a *positive* value that looks like a 1970 epoch.
The backend therefore trusts a live timestamp only when it sits close to the
host clock; otherwise it substitutes a host-clock estimate (the ESP32 counter
mapping for CSI, receive time for smoke).

These are pure functions with no I/O so the policy is easy to unit-test.  The
NTP probe in :mod:`app.ntp` is deliberately separate: connectivity is shown in
the UI, while correction is driven by the value itself.
"""

from __future__ import annotations

import math

# 2020-01-01T00:00:00Z — anything older is certainly a pre-NTP epoch
# (milliseconds-since-boot values are many orders of magnitude smaller).
TIMESTAMP_MIN_VALID_EPOCH = 1_577_836_800.0


def is_plausible_timestamp(
    raw: float | None, now: float, max_skew_s: float
) -> bool:
    """Return True when ``raw`` can be trusted as a live UNIX timestamp.

    A value is plausible when it is finite, at or after 2020-01-01, and within
    ``max_skew_s`` seconds of ``now`` (the host clock).
    """
    if raw is None or not math.isfinite(raw):
        return False
    if raw < TIMESTAMP_MIN_VALID_EPOCH:
        return False
    return abs(raw - now) <= max_skew_s


def select_timestamp(
    raw: float | None,
    fallback: float,
    now: float,
    max_skew_s: float,
) -> tuple[float, bool]:
    """Choose the raw timestamp when plausible, else the host fallback.

    Returns ``(timestamp, corrected)`` where ``corrected`` is True when the
    fallback was used.  ``fallback`` is the caller's host-clock estimate
    (counter-based for CSI, receive time for smoke).
    """
    if is_plausible_timestamp(raw, now, max_skew_s):
        return float(raw), False  # type: ignore[arg-type]
    return float(fallback), True
