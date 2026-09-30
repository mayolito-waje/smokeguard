"""Smoking-activity detection: rolling buffers, dummy trigger, snapshotting.

The real recognition pipeline (17 s smoke/VOC → SVM, 7 s CSI → ResNet-LSTM,
weighted box fusion) does not exist yet.  This module implements everything
around it — rolling windows, the trigger scheduler, SQLite persistence and
the WebSocket alert — with a dummy detector behind a single seam method
(``DetectionManager.detect``) that the real models will later replace.
"""

import asyncio
import json
import logging
import random
import time
from collections import deque

import numpy as np

from app.config import Settings
from app.detection_store import DetectionStore
from app.models import CSIRecord, SmokeRecord, WSAlert
from app.websocket_manager import ConnectionManager

logger = logging.getLogger("smokeguard")

# Hard caps on the rolling buffers (replay runs ~200 Hz, live ~100 Hz / 1 Hz)
_MAX_CSI_BUFFERED = 2000
_MAX_SMOKE_BUFFERED = 60

# Buffer windows are held slightly longer than the snapshot windows so a
# trigger at any moment can always fill its full window.
_WINDOW_SLACK = 1.2


class DetectionManager:
    """Feeds rolling CSI/smoke windows and fires the (dummy) detector."""

    def __init__(
        self,
        settings: Settings,
        store: DetectionStore,
        ws_manager: ConnectionManager,
    ) -> None:
        self._settings = settings
        self._store = store
        self._ws_manager = ws_manager
        self._csi: deque[CSIRecord] = deque()
        self._smoke: deque[SmokeRecord] = deque()

    # ------------------------------------------------------------------
    # Buffer feeding (called from the consumer tasks in main.py)
    # ------------------------------------------------------------------

    @property
    def has_buffered_csi(self) -> bool:
        """True when CSI frames are buffered (simulate pre-check)."""
        return bool(self._csi)

    def add_csi(self, record: CSIRecord) -> None:
        """Append a CSI frame and drop anything older than the window."""
        self._csi.append(record)
        self._prune()

    def add_smoke(self, record: SmokeRecord) -> None:
        """Append a smoke sample and drop anything older than the window."""
        self._smoke.append(record)
        self._prune()

    def _prune(self) -> None:
        # Prune relative to the LATEST record's timestamp (≈ wall clock for
        # live data) so CSV replay works: replayed rows carry the original
        # capture timestamps, which may be arbitrarily far from time.time().
        if self._csi:
            csi_cutoff = (
                self._csi[-1].timestamp_real
                - self._settings.detection_csi_seconds * _WINDOW_SLACK
            )
            while self._csi and self._csi[0].timestamp_real < csi_cutoff:
                self._csi.popleft()
        while len(self._csi) > _MAX_CSI_BUFFERED:
            self._csi.popleft()

        if self._smoke:
            smoke_cutoff = (
                self._smoke[-1].timestamp_real
                - self._settings.detection_smoke_seconds * _WINDOW_SLACK
            )
            while self._smoke and self._smoke[0].timestamp_real < smoke_cutoff:
                self._smoke.popleft()
        while len(self._smoke) > _MAX_SMOKE_BUFFERED:
            self._smoke.popleft()

    # ------------------------------------------------------------------
    # Dummy trigger scheduler
    # ------------------------------------------------------------------

    async def run_trigger_loop(self) -> None:
        """Fire the dummy detector at random intervals when enabled."""
        while True:
            await asyncio.sleep(
                random.uniform(
                    self._settings.dummy_trigger_min_s,
                    self._settings.dummy_trigger_max_s,
                )
            )
            if not self._store.get_enabled():
                continue
            if not self._csi:
                continue
            await self.trigger()

    # ------------------------------------------------------------------
    # Trigger point
    # ------------------------------------------------------------------

    async def trigger(self) -> dict | None:
        """Snapshot the windows, run the detector, persist + broadcast on hit.

        Returns the event summary when a detection fired, None otherwise.
        Called by the scheduler (enabled only) and by POST /api/detection/
        simulate (manual override, works regardless of the enabled flag).
        """
        csi_snap, csi_meta = self._snapshot_csi()
        smoke_snap = self._snapshot_smoke()

        if not await self.detect(csi_snap, smoke_snap):
            return None

        detected_at = time.time()
        csi_json = json.dumps(
            {
                "subcarrier_count": csi_meta["subcarrier_count"],
                "active_indices": csi_meta["active_indices"],
                "frames": csi_snap,
            }
        )
        smoke_json = json.dumps({"samples": smoke_snap})

        event_id = self._store.insert_event(
            detected_at=detected_at,
            csi_frames=len(csi_snap),
            smoke_samples=len(smoke_snap),
            csi_json=csi_json,
            smoke_json=smoke_json,
        )
        await self._ws_manager.broadcast_sync(
            WSAlert(id=event_id, detected_at=detected_at).model_dump()
        )
        logger.info(
            "Smoking activity detected (dummy): event #%d at %.3f "
            "(%d CSI frames, %d smoke samples)",
            event_id, detected_at, len(csi_snap), len(smoke_snap),
        )
        return {
            "id": event_id,
            "detected_at": detected_at,
            "csi_frames": len(csi_snap),
            "smoke_samples": len(smoke_snap),
        }

    async def detect(self, csi: list[dict], smoke: list[dict]) -> bool:
        """DUMMY DETECTOR — the seam for the real recognition models.

        Receives exactly the 7 s / 17 s windows the future SVM + ResNet-LSTM
        + weighted-box-fusion pipeline will consume, so only this method body
        changes once the models are trained.
        """
        return bool(csi)

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def _snapshot_csi(self) -> tuple[list[dict], dict]:
        """Amplitude-only, active-subcarriers-only window of recent frames.

        The visualization pipeline (CSI_RESEARCH.ipynb cell 20) consumes
        amplitude only, so I/Q is dropped here to keep events small.  The
        real model will read full CSIRecords from the in-memory deques at
        the detect() seam instead.
        """
        if not self._csi:
            return [], {"subcarrier_count": 0, "active_indices": []}
        cutoff = (
            self._csi[-1].timestamp_real - self._settings.detection_csi_seconds
        )
        records = [r for r in self._csi if r.timestamp_real >= cutoff]

        # metadata can only be None for degenerate (len == 0) records
        meta = records[0].subcarrier_metadata or {
            "data_start_idx": 0,
            "data_end_idx": -1,
            "data_upper_start": 1,
            "data_upper_end": 0,
        }
        active = list(
            range(meta["data_start_idx"], meta["data_end_idx"] + 1)
        ) + list(range(meta["data_upper_start"], meta["data_upper_end"] + 1))

        frames: list[dict] = []
        for r in records:
            i_arr = np.asarray(r.i_samples, dtype=np.float64)
            q_arr = np.asarray(r.q_samples, dtype=np.float64)
            amp = np.sqrt(i_arr**2 + q_arr**2)[active]
            frames.append(
                {
                    "t": r.timestamp_real,
                    "amp": [round(float(v), 2) for v in amp],
                }
            )
        return frames, {
            "subcarrier_count": records[0].subcarrier_count,
            "active_indices": active,
        }

    def _snapshot_smoke(self) -> list[dict]:
        """Window of recent smoke samples (BME680 fields null when absent)."""
        if not self._smoke:
            return []
        cutoff = (
            self._smoke[-1].timestamp_real
            - self._settings.detection_smoke_seconds
        )
        return [
            r.model_dump()
            for r in self._smoke
            if r.timestamp_real >= cutoff
        ]
