"""Unit tests for the experimental CSI-only class and the alert cooldown."""

import asyncio
import json
from types import SimpleNamespace

import numpy as np
import pytest

from app import inference
from app.detection import DetectionManager, escalation_allowed
from app.models import SmokeRecord

SENSOR_THRESHOLD = 0.4049989698494056
BELOW_GATE = 0.20  # sensor operating, but no smoking detected


def _csi(hit: bool = True, p_smoke: float = 0.8) -> inference.CsiResult:
    num_classes = len(inference.CSI_CLASSES)
    classes = np.full(num_classes, 1.0 / num_classes)
    index = (
        inference.CSI_SMOKE_INDEX
        if hit
        else inference.CSI_CLASSES.index("eat")
    )
    classes[index] = 0.9
    return inference.CsiResult(
        logits=np.zeros(num_classes),
        calibrated=classes,
        p_smoke=p_smoke,
        hit=hit,
        pred_class="smoke" if hit else "eat",
    )


# ---------------------------------------------------------------------------
# CSI class / calibrator contract
# ---------------------------------------------------------------------------

def test_csi_class_contract_is_seven_class():
    assert inference.CSI_CLASSES == [
        "smoke", "eat", "drink", "sit", "stand", "random", "static",
    ]
    assert inference.CSI_SMOKE_INDEX == 0


def _platt_payload(classes=None) -> dict:
    classes = list(classes or inference.CSI_CLASSES)
    num_classes = len(classes)
    return {
        "schema_version": 2,
        "classes": classes,
        "multiclass": {
            "classes": classes,
            "slope_A": [1.0] * num_classes,
            "intercept_B": [-1.0] * num_classes,
        },
        "smoke_binary": {"slope_A": 1.5, "intercept_B": -3.5},
    }


def test_platt_loader_reads_matching_classes(tmp_path):
    path = tmp_path / "cal.platt.json"
    path.write_text(json.dumps(_platt_payload()), encoding="utf-8")
    calibration = inference.load_platt_calibration(str(path))
    assert calibration["slopes"].shape == (len(inference.CSI_CLASSES),)
    assert calibration["intercepts"].shape == (len(inference.CSI_CLASSES),)
    assert calibration["smoke"] == (1.5, -3.5)


def test_platt_loader_rejects_class_mismatch(tmp_path):
    path = tmp_path / "cal.platt.json"
    path.write_text(
        json.dumps(_platt_payload(classes=["smoke", "eat"])), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        inference.load_platt_calibration(str(path))


def _fuse(**overrides):
    kwargs = dict(
        csi=_csi(),
        p_sensor=BELOW_GATE,
        csi_weight=0.5,
        score_threshold=0.70,
        sensor_threshold=SENSOR_THRESHOLD,
        csi_only_enabled=True,
    )
    kwargs.update(overrides)
    return inference.fuse(**kwargs)


# ---------------------------------------------------------------------------
# fuse(): CSI-only branch
# ---------------------------------------------------------------------------

def test_csi_only_disabled_returns_none():
    assert _fuse(csi_only_enabled=False) is None


def test_csi_only_fires_with_sensor_below_gate():
    decision = _fuse()
    assert decision is not None
    assert decision.label == "csi_only"
    assert decision.csi_hit is True
    assert decision.sensor_hit is False
    assert decision.fused_score is None
    assert decision.p_csi == 0.8
    assert decision.p_sensor == BELOW_GATE


def test_csi_only_requires_a_working_sensor():
    # p_sensor None means "sensor unavailable", not "sensor said no".
    assert _fuse(p_sensor=None) is None


def test_csi_only_requires_csi_hit():
    assert _fuse(csi=_csi(hit=False, p_smoke=0.9)) is None


def test_csi_only_requires_csi_result():
    assert _fuse(csi=None) is None


def test_csi_only_min_prob_gate():
    assert _fuse(csi=_csi(p_smoke=0.4), csi_only_min_prob=0.5) is None
    decision = _fuse(csi=_csi(p_smoke=0.6), csi_only_min_prob=0.5)
    assert decision is not None and decision.label == "csi_only"


# ---------------------------------------------------------------------------
# fuse(): sensor-hit paths unchanged
# ---------------------------------------------------------------------------

def test_sensor_hit_with_csi_is_fusion():
    decision = _fuse(p_sensor=0.95)
    assert decision is not None
    assert decision.label == "fusion"
    assert decision.fused_score == 0.5 * 0.8 + 0.5 * 0.95


def test_sensor_hit_without_csi_is_smoke_only():
    decision = _fuse(p_sensor=0.95, csi=None)
    assert decision is not None
    assert decision.label == "smoke_only"


# ---------------------------------------------------------------------------
# Cooldown: higher-ranked alerts may escalate
# ---------------------------------------------------------------------------

ANCHOR = 1_000_000.0
COOLDOWN_S = 30.0


class _Store:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.deleted: list[int] = []

    def get_setting(self, key):
        return None

    def set_setting(self, key, value):
        pass

    def get_csi_only_enabled(self) -> bool:
        return False

    def insert_event(self, **kwargs) -> int:
        self.events.append(kwargs)
        return len(self.events)

    def delete_event(self, event_id: int) -> bool:
        self.deleted.append(event_id)
        return True


class _WsManager:
    async def broadcast_sync(self, message) -> None:
        pass


class _StubManager(DetectionManager):
    """DetectionManager whose inference returns a configurable decision."""

    label = "fusion"

    async def detect(self, csi_records, smoke_window, baseline, t0):
        fusion = self.label == "fusion"
        return inference.FusionDecision(
            label=self.label,
            p_csi=0.9 if fusion else 0.2,
            p_sensor=0.9,
            fused_score=0.9 if fusion else 0.4,
            csi_hit=fusion,
            sensor_hit=True,
        )


def _smoke(t: float) -> SmokeRecord:
    return SmokeRecord(
        timestamp_real=t,
        pm1_0=1, pm2_5=2, pm10=3,
        cnt0_3=0, cnt0_5=0, cnt1_0=0, cnt2_5=0, cnt5_0=0, cnt10=0,
        rssi=-50,
    )


def _manager(seconds_since_last_alert: float | None, label: str = "fusion"):
    settings = SimpleNamespace(
        detection_smoke_seconds=14.0,
        detection_csi_max_seconds=7.0,
        detection_cooldown_s=COOLDOWN_S,
        detection_sensor_baseline_seconds=600.0,
        detection_csi_min_seconds=4.0,
    )
    store = _Store()
    manager = _StubManager(settings, store, _WsManager())
    manager.label = label
    manager._smoke.append(_smoke(ANCHOR - 14.5))  # covers t0 = anchor - 14.0
    manager._smoke.append(_smoke(ANCHOR))
    if seconds_since_last_alert is not None:
        manager._last_alert_anchor = ANCHOR - seconds_since_last_alert
    return manager, store


def test_escalation_outside_cooldown_always_allowed():
    assert escalation_allowed(False, "fusion", "csi_only")
    assert escalation_allowed(False, None, "csi_only")


def test_escalation_matrix_during_cooldown():
    assert escalation_allowed(True, None, "csi_only")
    assert not escalation_allowed(True, "csi_only", "csi_only")
    assert escalation_allowed(True, "csi_only", "smoke_only")
    assert escalation_allowed(True, "csi_only", "fusion")
    assert not escalation_allowed(True, "smoke_only", "csi_only")
    assert escalation_allowed(True, "smoke_only", "fusion")
    assert not escalation_allowed(True, "fusion", "fusion")
    assert not escalation_allowed(True, "fusion", "smoke_only")
    assert not escalation_allowed(True, "fusion", "csi_only")


def test_fusion_escalates_over_smoke_only_and_deletes_it():
    manager, store = _manager(seconds_since_last_alert=2.0)
    manager._last_alert_label = "smoke_only"
    manager._last_alert_event_id = 7

    event = asyncio.run(manager.trigger())

    assert event is not None
    assert event["label"] == "fusion"
    assert store.deleted == [7]      # superseded smoke_only removed
    assert len(store.events) == 1    # fusion stored as its replacement


def test_fusion_outside_cooldown_keeps_previous_event():
    manager, store = _manager(seconds_since_last_alert=COOLDOWN_S + 1.0)
    manager._last_alert_label = "smoke_only"
    manager._last_alert_event_id = 7

    event = asyncio.run(manager.trigger())

    assert event is not None
    assert store.deleted == []       # no cooldown => no supersede
    assert len(store.events) == 1


def test_smoke_only_escalates_over_csi_only_without_deleting():
    manager, store = _manager(seconds_since_last_alert=2.0, label="smoke_only")
    manager._last_alert_label = "csi_only"
    manager._last_alert_event_id = 7

    event = asyncio.run(manager.trigger())

    assert event is not None
    assert event["label"] == "smoke_only"
    assert store.deleted == []


def test_fusion_does_not_escalate_over_fusion():
    manager, store = _manager(seconds_since_last_alert=2.0)
    manager._last_alert_label = "fusion"
    manager._last_alert_event_id = 7

    assert asyncio.run(manager.trigger()) is None
    assert store.events == [] and store.deleted == []


def test_smoke_only_does_not_escalate_over_smoke_only():
    manager, store = _manager(seconds_since_last_alert=2.0, label="smoke_only")
    manager._last_alert_label = "smoke_only"
    manager._last_alert_event_id = 7

    assert asyncio.run(manager.trigger()) is None
    assert store.events == [] and store.deleted == []


def test_alert_fires_once_cooldown_expires():
    manager, store = _manager(seconds_since_last_alert=COOLDOWN_S + 1.0)
    event = asyncio.run(manager.trigger())
    assert event is not None
    assert event["label"] == "fusion"
    assert len(store.events) == 1
    assert store.deleted == []
