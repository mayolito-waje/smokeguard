"""Smoking-activity detection: rolling buffers, ML pipeline, snapshotting.

The real recognition pipeline lives in :mod:`app.inference` (CSI ResNet-LSTM
+ smoke SVM + late fusion).  This module owns everything around it — rolling
windows, the fixed-interval evaluation loop, alert cooldown, static-background
capture, SQLite persistence and the WebSocket alert.
"""

import asyncio
import json
import logging
import time
from collections import deque

import numpy as np

from app import inference
from app.config import Settings
from app.detection_store import DetectionStore
from app.models import CSIRecord, SmokeRecord, WSAlert
from app.websocket_manager import ConnectionManager

logger = logging.getLogger("smokeguard")

# Hard caps on the rolling buffers (replay runs ~200 Hz, live ~100 Hz CSI and
# ~1.7 Hz smoke).  The smoke buffer must cover the 14 s window + 600 s causal
# baseline (~1055 samples at 0.58 s cadence); CSI covers the scan range
# (up to detection_csi_max_seconds).
_MAX_CSI_BUFFERED = 4096
_MAX_SMOKE_BUFFERED = 2048

# Buffer windows are held slightly longer than the snapshot windows so a
# trigger at any moment can always fill its full window.
_WINDOW_SLACK = 1.2

# settings-table key for the persisted static background (JSON).
_BASELINE_SETTING_KEY = "csi_baseline"

# Highest alert rank (fusion) — nothing can escalate above it.
_TOP_LABEL_RANK = max(inference.LABEL_RANK.values())


def escalation_allowed(
    in_cooldown: bool, last_label: str | None, new_label: str
) -> bool:
    """Whether a new decision may alert while the previous alert is cooling.

    Outside a cooldown everything is allowed; inside it, only a strictly
    higher-ranked class (csi_only < smoke_only < fusion) may escalate.
    """
    if not in_cooldown:
        return True
    return inference.label_rank(new_label) > inference.label_rank(last_label)


class BaselineCaptureError(Exception):
    """Raised when there is not enough buffered CSI to capture a baseline."""


class DetectionManager:
    """Feeds rolling CSI/smoke windows and runs the real detector."""

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

        # Static background (52,) used by the CSI model, persisted across
        # restarts in the settings table.
        self._background: np.ndarray | None = None
        self._background_info: dict | None = None
        self._restore_background()

        # Cooldown is tracked in the record-timestamp domain so CSV replay
        # and live data behave identically.  The label and event id are
        # remembered so a higher-ranked class may escalate during the
        # cooldown and supersede (delete) the weaker alert it confirms.
        self._last_alert_anchor: float | None = None
        self._last_alert_label: str | None = None
        self._last_alert_event_id: int | None = None
        # Wall-clock throttle for the experimental CSI-only scan.
        self._last_csi_only_check: float | None = None
        self._trigger_lock = asyncio.Lock()
        self._warmup_notice_logged = False

    def _restore_background(self) -> None:
        raw = self._store.get_setting(_BASELINE_SETTING_KEY)
        if not raw:
            return
        try:
            data = json.loads(raw)
            vector = np.asarray(data["vector"], dtype=np.float64)
            if vector.shape != (len(inference.ACTIVE_INDICES_64),):
                raise ValueError(f"unexpected baseline shape {vector.shape}")
            self._background = vector
            self._background_info = {
                "captured_at": float(data["captured_at"]),
                "packet_count": int(data["packet_count"]),
                "csi_seconds": float(data.get("csi_seconds", 0.0)),
            }
            logger.info(
                "Restored static background (%d packets, captured %.0f)",
                self._background_info["packet_count"],
                self._background_info["captured_at"],
            )
        except Exception:
            logger.warning("Ignoring unreadable persisted CSI baseline", exc_info=True)

    # ------------------------------------------------------------------
    # Buffer feeding (called from the consumer tasks in main.py)
    # ------------------------------------------------------------------

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
                - self._settings.detection_smoke_seconds * _WINDOW_SLACK
            )
            while self._csi and self._csi[0].timestamp_real < csi_cutoff:
                self._csi.popleft()
        while len(self._csi) > _MAX_CSI_BUFFERED:
            self._csi.popleft()

        if self._smoke:
            smoke_cutoff = (
                self._smoke[-1].timestamp_real
                - (
                    self._settings.detection_smoke_seconds
                    + self._settings.detection_sensor_baseline_seconds
                )
                * _WINDOW_SLACK
            )
            while self._smoke and self._smoke[0].timestamp_real < smoke_cutoff:
                self._smoke.popleft()
        while len(self._smoke) > _MAX_SMOKE_BUFFERED:
            self._smoke.popleft()

    # ------------------------------------------------------------------
    # Models
    # ------------------------------------------------------------------

    async def warmup_models(self) -> None:
        """Load the ML models off the event loop (called from lifespan)."""
        await asyncio.to_thread(
            inference.load_models,
            self._settings.csi_model_path,
            self._settings.smoke_model_path,
            self._settings.csi_platt_path,
        )
        if inference.models_ready():
            logger.info("Detection pipeline ready (models loaded)")
        else:
            logger.warning(
                "Detection pipeline degraded — one or more models failed to load"
            )

    # ------------------------------------------------------------------
    # Static background capture
    # ------------------------------------------------------------------

    def background_status(self) -> dict | None:
        """Persisted static-background info, or None when never captured."""
        return dict(self._background_info) if self._background_info else None

    async def capture_background(self) -> dict:
        """Capture the static CSI background from the most recent packets.

        Runs the notebook's denoising chain over the latest
        ``detection_baseline_min_packets`` frames and stores their time-mean
        as the (52,) background vector.
        """
        min_packets = self._settings.detection_baseline_min_packets
        if len(self._csi) < min_packets:
            raise BaselineCaptureError(
                f"Need at least {min_packets} buffered CSI packets "
                f"(currently {len(self._csi)})"
            )

        # Capture ~420 packets (one model window), bounded by the
        # buffer size; the minimum above guarantees a usable span.
        capture_count = min(inference.N_CSI_POINTS, len(self._csi))
        records = list(self._csi)[-capture_count:]
        amp, t = inference.extract_amplitude_matrix(records)
        if len(t) < min_packets:
            raise BaselineCaptureError(
                f"Need at least {min_packets} usable 64-subcarrier packets "
                f"(currently {len(t)})"
            )

        background = await asyncio.to_thread(
            inference.capture_background, t, amp
        )
        info = {
            "captured_at": time.time(),
            "packet_count": int(len(t)),
            "csi_seconds": float(t[-1] - t[0]),
        }
        self._background = background
        self._background_info = info
        self._store.set_setting(
            _BASELINE_SETTING_KEY,
            json.dumps(
                {
                    **info,
                    "vector": [round(float(v), 6) for v in background],
                }
            ),
        )
        logger.info(
            "Static background captured: %d packets over %.2f s",
            info["packet_count"], info["csi_seconds"],
        )
        return info

    # ------------------------------------------------------------------
    # Evaluation loop
    # ------------------------------------------------------------------

    async def run_trigger_loop(self) -> None:
        """Evaluate the pipeline every ``detection_interval_s`` when enabled."""
        while True:
            await asyncio.sleep(self._settings.detection_interval_s)
            if not self._store.get_enabled():
                continue
            if not self._smoke:
                continue
            # fusion/smoke_only and the experimental csi_only class all need
            # the sensor model (csi_only = "sensor operating but below its
            # gate"); the CSI model only upgrades the class or is optional.
            if not inference.sensor_model_ready():
                if not self._warmup_notice_logged:
                    logger.info("Detection loop waiting for models to load")
                    self._warmup_notice_logged = True
                continue
            self._warmup_notice_logged = False
            await self.trigger()

    # ------------------------------------------------------------------
    # Trigger point
    # ------------------------------------------------------------------

    async def trigger(self) -> dict | None:
        """Evaluate the current windows, persist + broadcast on a hit.

        The evaluation window is anchored to the latest smoke sample
        (``anchor``): smoke = [anchor − 14.905 s, anchor], CSI = a window
        starting at the same instant (t0) whose duration is scanned over
        4.0–7.0 s (smoking gestures vary), sensor baseline = the 600 s before
        the window start.

        Returns the event summary when a detection fired, None otherwise.
        Called by the evaluation loop (cooldown enforced).
        """
        async with self._trigger_lock:
            if not self._smoke:
                return None

            settings = self._settings
            anchor = self._smoke[-1].timestamp_real
            t0 = anchor - settings.detection_smoke_seconds
            t1_csi_max = t0 + settings.detection_csi_max_seconds

            # Never re-alert the exact same window: if the stream stopped
            # (mockup finished / sensor offline) the anchor stays frozen and
            # the cooldown alone would keep inserting duplicate events.
            if (
                self._last_alert_anchor is not None
                and anchor <= self._last_alert_anchor
            ):
                return None

            # The sensor model was trained on full 14 s windows — never
            # evaluate before the buffer covers the whole window (startup /
            # mockup pre-roll).
            if self._smoke[0].timestamp_real > t0:
                return None

            in_cooldown = (
                self._last_alert_anchor is not None
                and anchor - self._last_alert_anchor
                < settings.detection_cooldown_s
            )
            # A confirmed alert cannot be improved on during its cooldown —
            # skip the (expensive) inference entirely.
            if (
                in_cooldown
                and inference.label_rank(self._last_alert_label) >= _TOP_LABEL_RANK
            ):
                return None

            smoke_window = [
                r for r in self._smoke if r.timestamp_real >= t0
            ]
            baseline = [
                r
                for r in self._smoke
                if t0 - settings.detection_sensor_baseline_seconds
                <= r.timestamp_real
                < t0
            ]
            csi_records = [
                r
                for r in self._csi
                if t0 <= r.timestamp_real <= t1_csi_max + 1e-6
            ]

            decision = await self.detect(
                csi_records, smoke_window, baseline, t0
            )
            if decision is None:
                return None

            # During a cooldown only a strictly higher-ranked class may
            # escalate (csi_only → smoke_only → fusion): a confirmation, not
            # a duplicate.
            if not escalation_allowed(
                in_cooldown, self._last_alert_label, decision.label
            ):
                return None

            # A fusion confirmation supersedes the sensor-only alert it
            # validated — the weaker history entry is deleted after the
            # replacement is stored.
            superseded_id: int | None = None
            if (
                in_cooldown
                and decision.label == "fusion"
                and self._last_alert_label == "smoke_only"
            ):
                superseded_id = self._last_alert_event_id

            self._last_alert_anchor = anchor
            self._last_alert_label = decision.label
            detected_at = time.time()

            # Snapshot the exact windows that produced this decision (the
            # Detail view consumes the amplitude-only v2 contract).  When the
            # CSI model did not run (no background / no valid candidate) the
            # snapshot keeps the minimum window length.
            snapshot_seconds = (
                decision.csi_window_seconds
                or settings.detection_csi_min_seconds
            )
            csi_snap, csi_meta = self._snapshot_csi(
                t0, t0 + snapshot_seconds
            )
            smoke_snap = self._snapshot_smoke(t0, anchor)
            csi_json = json.dumps(
                {
                    # v2 = ESP-IDF FFT-bin subcarrier mapping (DC at index 0);
                    # v1 events (pre-fix) stored the old ascending-order indices.
                    "snapshot_version": 2,
                    "subcarrier_count": csi_meta["subcarrier_count"],
                    "active_indices": csi_meta["active_indices"],
                    "window_seconds": round(float(snapshot_seconds), 3),
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
                label=decision.label,
                p_csi=decision.p_csi,
                p_sensor=decision.p_sensor,
                fused_score=decision.fused_score,
            )
            self._last_alert_event_id = event_id
            if superseded_id is not None and superseded_id != event_id:
                try:
                    self._store.delete_event(superseded_id)
                    logger.info(
                        "Superseded smoke_only event #%d with fusion event #%d",
                        superseded_id, event_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to delete superseded event #%d", superseded_id
                    )
            await self._ws_manager.broadcast_sync(
                WSAlert(
                    id=event_id,
                    detected_at=detected_at,
                    label=decision.label,
                    p_csi=decision.p_csi,
                    p_sensor=decision.p_sensor,
                    fused_score=decision.fused_score,
                ).model_dump()
            )
            logger.info(
                "Smoking activity detected (%s): event #%d at %.3f "
                "(p_csi=%s, p_sensor=%.3f, score=%s, csi_window=%s; "
                "%d CSI frames, %d smoke samples)",
                decision.label,
                event_id,
                detected_at,
                f"{decision.p_csi:.3f}" if decision.p_csi is not None else "n/a",
                decision.p_sensor if decision.p_sensor is not None else 0.0,
                f"{decision.fused_score:.3f}"
                if decision.fused_score is not None
                else "n/a",
                f"{snapshot_seconds:.1f}s",
                len(csi_snap),
                len(smoke_snap),
            )
            return {
                "id": event_id,
                "detected_at": detected_at,
                "csi_frames": len(csi_snap),
                "smoke_samples": len(smoke_snap),
                "label": decision.label,
                "p_csi": decision.p_csi,
                "p_sensor": decision.p_sensor,
                "fused_score": decision.fused_score,
                "csi_window_seconds": snapshot_seconds,
            }

    async def detect(
        self,
        csi_records: list[CSIRecord],
        smoke_window: list[SmokeRecord],
        baseline: list[SmokeRecord],
        t0: float,
    ) -> inference.FusionDecision | None:
        """Run the real recognition pipeline on one aligned window.

        The sensor model runs first.  A sensor hit is required for
        ``fusion``/``smoke_only``; when the sensor operates but stays below
        its gate, the experimental ``csi_only`` class may fire if enabled
        (throttled to ``detection_csi_only_interval_s`` because its scan is
        expensive).  Without the CSI model/background any alarm degrades to
        ``smoke_only``.  Inference errors degrade to the sensor-only path
        rather than raising out of the evaluation loop.
        """
        settings = self._settings

        p_sensor: float | None = None
        if (
            len(smoke_window) >= settings.detection_smoke_min_window
            and len(baseline) >= settings.detection_smoke_min_baseline
        ):
            try:
                sensor_features = await asyncio.to_thread(
                    inference.extract_sensor_features, smoke_window, baseline
                )
                if sensor_features is not None:
                    p_sensor = await asyncio.to_thread(
                        inference.predict_sensor, sensor_features
                    )
            except Exception:
                logger.exception("Sensor inference failed for this window")
                p_sensor = None

        sensor_hit = (
            p_sensor is not None
            and p_sensor >= settings.sensor_prob_threshold
        )
        csi_only_enabled = self._store.get_csi_only_enabled()

        if not sensor_hit:
            # Fast idle path: no class can fire without the sensor unless
            # the experimental CSI-only mode is on and the sensor is
            # actually operating (None means "unavailable", not "no smoke").
            if not csi_only_enabled or p_sensor is None:
                return None
            if not (
                csi_records
                and self._background is not None
                and inference.csi_model_ready()
            ):
                return None
            # Throttle the CSI-only scan (wall clock: a frozen mock/replay
            # anchor must still be re-scanned as CSI arrives).  Do not
            # consume the interval on a tick with no usable CSI.
            now = time.monotonic()
            if (
                self._last_csi_only_check is not None
                and now - self._last_csi_only_check
                < settings.detection_csi_only_interval_s
            ):
                return None
            self._last_csi_only_check = now

        csi_result, csi_window = await self._run_csi_scan(csi_records, t0)
        return inference.fuse(
            csi_result,
            p_sensor,
            settings.fusion_csi_weight,
            settings.fusion_score_threshold,
            settings.sensor_prob_threshold,
            csi_window_seconds=csi_window,
            csi_only_enabled=csi_only_enabled,
            csi_only_min_prob=settings.detection_csi_only_min_prob,
        )

    async def _run_csi_scan(
        self, csi_records: list[CSIRecord], t0: float
    ) -> tuple[inference.CsiResult | None, float | None]:
        """Extract and scan the CSI window; degrade to no CSI on failure."""
        csi_result: inference.CsiResult | None = None
        csi_window: float | None = None
        if (
            csi_records
            and self._background is not None
            and inference.csi_model_ready()
        ):
            try:
                amp, t = inference.extract_amplitude_matrix(csi_records)
                if len(t) >= self._settings.detection_csi_min_packets:
                    csi_result, csi_window = await self._scan_csi(t, amp, t0)
            except Exception:
                logger.exception(
                    "CSI inference failed for this window — using sensor only"
                )
                csi_result = None
                csi_window = None
        return csi_result, csi_window

    async def _scan_csi(
        self, t: np.ndarray, amp: np.ndarray, t0: float
    ) -> tuple[inference.CsiResult | None, float | None]:
        """Evaluate the CSI model over the candidate window durations.

        Every candidate starts at ``t0`` (the smoke window start) and is
        resampled to 420 points over its own duration, so the model sees a
        gesture-length-matched window.  Returns the winning (result, duration)
        by ``(hit, p_smoke)``; ``(None, None)`` when no candidate is usable.
        """
        settings = self._settings
        candidates = inference.csi_window_candidates(
            settings.detection_csi_min_seconds,
            settings.detection_csi_max_seconds,
            settings.detection_csi_step_seconds,
        )
        results: list[tuple[float, inference.CsiResult]] = []
        previous_cut = -1
        for duration in candidates:
            cut = int(np.searchsorted(t, t0 + duration + 1e-6, side="right"))
            # Identical prefix => identical prediction (the CSI stream is
            # still filling); skip the duplicate work.
            if cut == previous_cut:
                continue
            previous_cut = cut
            if cut < settings.detection_csi_min_packets:
                continue
            try:
                features = await asyncio.to_thread(
                    inference.preprocess_csi_window,
                    t[:cut],
                    amp[:cut],
                    self._background,
                )
                if not np.isfinite(features).all():
                    continue
                result = await asyncio.to_thread(
                    inference.predict_csi, features
                )
                if not (
                    np.isfinite(result.logits).all()
                    and np.isfinite(result.p_smoke)
                ):
                    continue
                results.append((duration, result))
            except Exception:
                logger.debug(
                    "CSI candidate %.1fs failed — skipping", duration,
                    exc_info=True,
                )
        best = inference.select_best_csi_candidate(results)
        if best is None:
            return None, None
        return best[1], best[0]

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def _snapshot_csi(self, t0: float, t1: float) -> tuple[list[dict], dict]:
        """Amplitude-only, active-subcarriers-only frames within [t0, t1].

        The visualization pipeline consumes amplitude only, so I/Q is dropped
        here to keep events small.  The model path reads full CSIRecords from
        the in-memory deques instead.
        """
        records = [r for r in self._csi if t0 <= r.timestamp_real <= t1]
        if not records:
            return [], {"subcarrier_count": 0, "active_indices": []}

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

    def _snapshot_smoke(self, t0: float, t1: float) -> list[dict]:
        """Smoke samples within [t0, t1] (BME680 fields null when absent)."""
        return [
            r.model_dump()
            for r in self._smoke
            if t0 <= r.timestamp_real <= t1
        ]
