"""Unit tests for the monthly PDF detection report and its data plumbing."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.detection_store import DetectionStore
from app.report import (
    _COLUMNS,
    _TABLE_WIDTH,
    _csi_hit,
    _fusion_hit,
    _sensor_hit,
    build_monthly_report_pdf,
    probability_level,
    summarize,
)
from app.routes import detection as detection_routes

SENSOR_THRESHOLD = 0.4049989698494056
FUSION_THRESHOLD = 0.70
LOGO = (
    Path(__file__).resolve().parents[1]
    / "frontend" / "public" / "smokeguard_logo.png"
)


def _event(**overrides) -> dict:
    event = {
        "id": 1,
        "detected_at": datetime(2026, 11, 2, 14, 32).timestamp(),
        "label": "fusion",
        "p_csi": 0.87,
        "p_sensor": 0.95,
        "fused_score": 0.91,
        "csi_hit": True,
        "sensor_hit": True,
        "avg_pm2_5": 15.2,
    }
    event.update(overrides)
    return event


# ---------------------------------------------------------------------------
# Report semantics
# ---------------------------------------------------------------------------

def test_probability_level_mapping():
    assert probability_level("fusion")[0] == "HIGH"
    assert probability_level("smoke_only")[0] == "MEDIUM"
    assert probability_level("csi_only")[0] == "LOW"
    # Unknown labels fall back to the weakest class.
    assert probability_level("other")[0] == "LOW"


def test_summarize_counts_levels():
    events = [
        _event(label="fusion"),
        _event(label="smoke_only"),
        _event(label="smoke_only"),
        _event(label="csi_only"),
    ]
    assert summarize(events) == {
        "total": 4, "HIGH": 1, "MEDIUM": 2, "LOW": 1,
    }


def test_highlight_gate_fallbacks_for_legacy_rows():
    # Stored flags win.
    assert _csi_hit({"csi_hit": False, "label": "fusion"}) is False
    assert _sensor_hit({"sensor_hit": False, "p_sensor": 0.99},
                       SENSOR_THRESHOLD) is False
    # Legacy fallbacks: labels, then the stored probability.
    assert _csi_hit({"csi_hit": None, "label": "fusion"}) is True
    assert _csi_hit({"csi_hit": None, "label": "csi_only"}) is True
    assert _csi_hit({"csi_hit": None, "label": "smoke_only"}) is False
    assert _sensor_hit({"sensor_hit": None, "label": "fusion"},
                       SENSOR_THRESHOLD) is True
    assert _sensor_hit({"sensor_hit": None, "label": "smoke_only"},
                       SENSOR_THRESHOLD) is True
    # csi_only is authoritative: the sensor stayed below the gate even when a
    # legacy/mock row stored a borderline probability.
    assert _sensor_hit({"sensor_hit": None, "label": "csi_only",
                        "p_sensor": 0.50}, SENSOR_THRESHOLD) is False
    # Unknown labels fall back to the stored probability.
    assert _sensor_hit({"sensor_hit": None, "p_sensor": 0.50},
                       SENSOR_THRESHOLD) is True
    assert _sensor_hit({"sensor_hit": None, "p_sensor": 0.20},
                       SENSOR_THRESHOLD) is False
    assert _sensor_hit({"sensor_hit": None, "p_sensor": None},
                       SENSOR_THRESHOLD) is False
    assert _fusion_hit({"fused_score": 0.71}, FUSION_THRESHOLD) is True
    assert _fusion_hit({"fused_score": 0.69}, FUSION_THRESHOLD) is False
    assert _fusion_hit({"fused_score": None}, FUSION_THRESHOLD) is False


def test_report_pdf_with_events_is_valid():
    events = [
        _event(id=1),
        _event(
            id=2,
            detected_at=datetime(2026, 11, 3, 9, 5).timestamp(),
            label="smoke_only", p_csi=0.2, fused_score=0.55,
            csi_hit=False,
        ),
        _event(
            id=3,
            detected_at=datetime(2026, 11, 4, 20, 15).timestamp(),
            label="csi_only", p_sensor=0.1, fused_score=None,
            sensor_hit=False, avg_pm2_5=None,
        ),
    ]
    pdf = build_monthly_report_pdf(
        year=2026,
        month=11,
        events=events,
        sensor_prob_threshold=SENSOR_THRESHOLD,
        fusion_score_threshold=FUSION_THRESHOLD,
        logo_path=None,
        generated_at=datetime(2026, 11, 17, 19, 45),
    )
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000


def test_report_pdf_empty_month_is_valid():
    pdf = build_monthly_report_pdf(
        year=2026,
        month=10,
        events=[],
        sensor_prob_threshold=SENSOR_THRESHOLD,
        fusion_score_threshold=FUSION_THRESHOLD,
        logo_path=None,
    )
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000


def test_report_table_has_no_facility_column_and_keeps_full_width():
    headings = [title for title, _, _ in _COLUMNS]
    assert "Facility" not in headings
    assert len(_COLUMNS) == 8
    assert [width for _, width, _ in _COLUMNS] == [
        32, 22, 52, 33, 33, 29, 27, 39,
    ]
    assert _TABLE_WIDTH == 267


def test_report_pdf_embeds_the_logo_when_available():
    if not LOGO.is_file():
        return
    pdf = build_monthly_report_pdf(
        year=2026,
        month=11,
        events=[_event()],
        sensor_prob_threshold=SENSOR_THRESHOLD,
        fusion_score_threshold=FUSION_THRESHOLD,
        logo_path=str(LOGO),
    )
    assert pdf.startswith(b"%PDF-")


# ---------------------------------------------------------------------------
# Store plumbing
# ---------------------------------------------------------------------------

def _insert_event(
    store: DetectionStore,
    detected_at: float,
    *,
    label: str = "fusion",
    csi_hit: bool | None = None,
    sensor_hit: bool | None = None,
    smoke_json: str | None = None,
) -> int:
    return store.insert_event(
        detected_at=detected_at,
        csi_frames=2,
        smoke_samples=2,
        csi_json=json.dumps({"snapshot_version": 2, "frames": []}),
        smoke_json=smoke_json
        or json.dumps({"samples": [{"pm2_5": 10}, {"pm2_5": 20}]}),
        label=label,
        p_csi=0.8,
        p_sensor=0.9,
        fused_score=0.85 if label != "csi_only" else None,
        csi_hit=csi_hit,
        sensor_hit=sensor_hit,
    )


def test_store_reports_rows_oldest_first_with_avg_pm2_5(tmp_path):
    store = DetectionStore(str(tmp_path / "detection.db"))
    try:
        later = datetime(2026, 11, 10, 12, 0).timestamp()
        earlier = datetime(2026, 11, 2, 8, 0).timestamp()
        _insert_event(
            store, later,
            smoke_json=json.dumps({"samples": [{"pm2_5": 30}, {"pm2_5": 40}]}),
        )
        _insert_event(store, earlier, label="smoke_only")

        events = store.list_events_for_report(2026, 11)

        assert [e["detected_at"] for e in events] == [earlier, later]
        assert events[0]["avg_pm2_5"] == 15.0
        assert events[1]["avg_pm2_5"] == 35.0
        assert events[0]["label"] == "smoke_only"
    finally:
        store.close()


def test_store_avg_pm2_5_is_none_without_samples(tmp_path):
    store = DetectionStore(str(tmp_path / "detection.db"))
    try:
        _insert_event(
            store, datetime(2026, 11, 2, 8, 0).timestamp(),
            smoke_json=json.dumps({"samples": []}),
        )
        events = store.list_events_for_report(2026, 11)
        assert events[0]["avg_pm2_5"] is None
    finally:
        store.close()


def test_store_migrates_old_schema_and_backfills_nothing_eagerly(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE detection_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at REAL NOT NULL,
            csi_frames INTEGER NOT NULL,
            smoke_samples INTEGER NOT NULL,
            csi_json TEXT NOT NULL,
            smoke_json TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO detection_events
            (detected_at, csi_frames, smoke_samples, csi_json, smoke_json,
             created_at)
        VALUES (1000.0, 1, 1, '{"frames": []}', '{"samples": []}', 1000.0);
        """
    )
    conn.commit()
    conn.close()

    store = DetectionStore(str(db_path))
    try:
        event = store.get_event(1)
        assert event is not None
        assert event["label"] == "fusion"
        assert event["csi_hit"] is None
        assert event["sensor_hit"] is None
        assert len(store.list_events_missing_hits()) == 1
    finally:
        store.close()


def test_update_event_hits_only_touches_requested_columns(tmp_path):
    store = DetectionStore(str(tmp_path / "detection.db"))
    try:
        event_id = _insert_event(
            store, datetime(2026, 11, 2, 8, 0).timestamp()
        )
        assert store.update_event_hits(event_id, sensor_hit=True) is True
        event = store.get_event(event_id)
        assert event["sensor_hit"] is True
        assert event["csi_hit"] is None
        assert store.update_event_hits(event_id, csi_hit=False) is True
        event = store.get_event(event_id)
        assert event["csi_hit"] is False
        assert event["sensor_hit"] is True
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

def test_report_route_returns_a_pdf(tmp_path):
    store = DetectionStore(str(tmp_path / "detection.db"))
    try:
        _insert_event(
            store, datetime(2026, 11, 2, 8, 0).timestamp()
        )
        app = FastAPI()
        app.include_router(detection_routes.router)
        app.state.detection_store = store
        app.state.settings = SimpleNamespace(
            sensor_prob_threshold=SENSOR_THRESHOLD,
            fusion_score_threshold=FUSION_THRESHOLD,
            report_logo_path=None,
        )
        with TestClient(app) as client:
            response = client.get(
                "/api/detection/report", params={"year": 2026, "month": 11}
            )
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/pdf"
            assert response.content.startswith(b"%PDF-")
            assert (
                "smokeguard-detection-report-2026-11.pdf"
                in response.headers["content-disposition"]
            )

            invalid = client.get(
                "/api/detection/report", params={"year": 2026, "month": 13}
            )
            assert invalid.status_code == 422
    finally:
        store.close()
