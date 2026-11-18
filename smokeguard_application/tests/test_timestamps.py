"""Unit tests for the live-timestamp plausibility policy."""

import time

from app.timestamps import (
    TIMESTAMP_MIN_VALID_EPOCH,
    is_plausible_timestamp,
    select_timestamp,
)

SKEW = 300.0


def test_boot_relative_and_pre_2020_values_are_corrected():
    now = time.time()
    for raw in (12.345678, 0.0, -5.0, 1_500_000_000.0, 1971_000_000.0):
        ts, corrected = select_timestamp(raw, now, now, SKEW)
        assert corrected is True
        assert ts == now


def test_non_finite_values_are_corrected():
    now = time.time()
    for raw in (None, float("nan"), float("inf"), float("-inf")):
        ts, corrected = select_timestamp(raw, now - 1.0, now, SKEW)
        assert corrected is True
        assert ts == now - 1.0


def test_recent_values_are_preserved():
    now = time.time()
    for raw in (now, now - 10, now + 10, now - 240, now + 240):
        ts, corrected = select_timestamp(raw, now, now, SKEW)
        assert corrected is False
        assert ts == raw


def test_values_outside_the_window_are_corrected():
    now = time.time()
    for raw in (now - 301, now + 301, now - 600, now + 600):
        ts, corrected = select_timestamp(raw, now, now, SKEW)
        assert corrected is True
        assert ts == now


def test_window_boundary_is_inclusive():
    now = time.time()
    assert is_plausible_timestamp(now - SKEW, now, SKEW) is True
    assert is_plausible_timestamp(now + SKEW, now, SKEW) is True
    assert is_plausible_timestamp(now - SKEW - 1e-6, now, SKEW) is False
    assert is_plausible_timestamp(now + SKEW + 1e-6, now, SKEW) is False


def test_pre_2020_floor():
    now = 2_000_000_000.0
    assert is_plausible_timestamp(TIMESTAMP_MIN_VALID_EPOCH, now, 10**9) is True
    assert (
        is_plausible_timestamp(TIMESTAMP_MIN_VALID_EPOCH - 1, now, 10**9)
        is False
    )
