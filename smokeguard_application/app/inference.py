"""Real recognition pipeline: CSI ResNet-LSTM + smoke SVM + late fusion.

This module ports the inference math from the training notebooks verbatim:

- ``SmokeGuard_v2.ipynb`` — CSI path: amplitude over the 52 active
  subcarriers → Hampel (7, 3σ, MAD) → linear resample to 420 points →
  Butterworth order-4 low-pass at 5 Hz (zero-phase) → Gaussian σ=2 →
  static-background subtraction → seven-class ResNet-LSTM logits → Platt
  calibration loaded from the v2 calibrator JSON.
- ``SmokeGuard_Smoke_Sensor.ipynb`` — sensor path: 14.905367895113585 s
  window (the mean training-interval duration frozen in the file split) +
  600 s causal baseline → 22 hand-crafted features → StandardScaler + SVC.
  ``models/smoke_svm.meta.json`` is the source of truth for the window,
  threshold, and feature contract.
- ``SmokeGuard_Fusion.ipynb`` — late fusion: weighted score with a CSI
  argmax gate and a sensor-probability gate, plus the user-mandated
  sensor-only fallback (labeled ``smoke_only``).  The notebook's 0.3/0.7
  weights were changed to 0.5/0.5 in deployment so a high sensor score
  can no longer carry a weak CSI hit over the fused threshold.  An experimental, weakest
  class (``csi_only``) covers a CSI gesture when the sensor operates but
  stays below its gate.

TensorFlow/sklearn are imported lazily (inside :func:`load_models`) so the
backend still serves the sensor-only path when TensorFlow is unavailable.
Every function here is synchronous; the async orchestration layer
(``app/detection.py``) calls them via ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.models import CSIRecord, SmokeRecord

logger = logging.getLogger("smokeguard")

# ---------------------------------------------------------------------------
# Constants frozen from the notebooks
# ---------------------------------------------------------------------------

# Active subcarriers of a 64-subcarrier CSI frame (LLTF nulls: 0 and 27..37).
ACTIVE_INDICES_64: list[int] = list(range(1, 27)) + list(range(38, 64))  # 52

# CSI model contract: every window is resampled to exactly 420 packets.
N_CSI_POINTS = 420

# Seven-class label order of the CSI model (SmokeGuard_v2.ipynb SEVEN_CLASSES).
# "smoke" is index 0; "static" (index 6) is the idle/background class added in
# the v2 model.  The Platt calibrator JSON must use this exact order.
CSI_CLASSES = ["smoke", "eat", "drink", "sit", "stand", "random", "static"]
CSI_SMOKE_INDEX = CSI_CLASSES.index("smoke")

# Denoising hyper-parameters (notebook cells 4/8).
HAMPEL_WINDOW = 7
HAMPEL_N_SIGMAS = 3
LOWPASS_HZ = 5.0
LOWPASS_ORDER = 4
GAUSSIAN_SIGMA = 2.0

# Schema of the notebook's Platt calibrator JSON
# (models/csi_resnet_lstm_7class.platt.json).
PLATT_SCHEMA_VERSION = 2

# Schema of the sensor notebook's provenance sidecar
# (models/smoke_svm.meta.json).
SENSOR_META_SCHEMA_VERSION = 2

# Sensor feature channels (BME680 + PMS5003; rssi/altitude excluded by design).
SENSOR_CHANNELS = [
    "temp_c", "humidity_pct", "pressure_hpa",
    "gas_kohm", "pm1_0", "pm2_5", "pm10",
]

# Feature vector order frozen in the notebook's `feature_config.feature_names`
# (models/smoke_svm.meta.json).  Per channel: mean − baseline median, std,
# last − first; then the gas baseline ratio.  Changing this requires retraining.
SENSOR_FEATURE_NAMES = [
    f"{channel}__{stat}"
    for channel in SENSOR_CHANNELS
    for stat in ("base_diff", "std", "first_last_diff")
] + ["gas_kohm__base_ratio"]

# Default sensor window / baseline lengths (seconds), frozen in the sensor
# notebook's split manifest.  The window is the mean training-interval
# duration (2026-11-08 file split), not a hand-picked value; retraining on a
# new split can change it, so keep it in sync with
# `models/smoke_svm.meta.json` (`preprocessing.window_seconds`).
SENSOR_WINDOW_SECONDS = 14.905367895113585
SENSOR_BASELINE_SECONDS = 600.0
SENSOR_MIN_WINDOW_SAMPLES = 8
SENSOR_MIN_BASELINE_SAMPLES = 30

# Deployed sensor gate: F1-max threshold on grouped out-of-fold training
# probabilities (SmokeGuard_Smoke_Sensor.ipynb, theta = 0.4049989698494056).
SENSOR_THRESHOLD = 0.4049989698494056


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class CsiResult:
    """One CSI window pushed through the ResNet-LSTM + Platt calibration."""

    logits: np.ndarray                # (7,) raw activity logits
    calibrated: np.ndarray            # (7,) row-normalized Platt probs
    p_smoke: float                    # binary Platt P(smoke)
    hit: bool                         # argmax(calibrated) == "smoke"
    pred_class: str

    @property
    def calibrated_probs(self) -> dict[str, float]:
        return {cls: float(p) for cls, p in zip(CSI_CLASSES, self.calibrated)}


# Alert classes ordered by confidence: csi_only (experimental, weakest) <
# smoke_only (sensor only) < fusion (CSI + sensor agree).  A higher rank may
# escalate a lower-ranked alert during its cooldown.
LABEL_RANK: dict[str, int] = {"csi_only": 0, "smoke_only": 1, "fusion": 2}


def label_rank(label: str | None) -> int:
    """Confidence rank of an alert class (unknown/None → -1)."""
    return LABEL_RANK.get(label, -1) if label is not None else -1


@dataclass
class FusionDecision:
    """Late-fusion outcome for one evaluation tick."""

    label: str                        # "csi_only" | "smoke_only" | "fusion"
    p_csi: float | None
    p_sensor: float | None
    fused_score: float | None         # None for csi_only (no fusion computed)
    csi_hit: bool
    sensor_hit: bool
    csi_pred_class: str | None = None
    calibrated_probs: dict[str, float] = field(default_factory=dict)
    csi_window_seconds: float | None = None  # duration of the winning window


# ---------------------------------------------------------------------------
# CSI amplitude extraction
# ---------------------------------------------------------------------------

def extract_amplitude_matrix(
    records: list[CSIRecord],
) -> tuple[np.ndarray, np.ndarray]:
    """Return (amp (N, 52), t (N,)) sorted by timestamp for 64-subcarrier frames.

    Records with a different subcarrier count are skipped (the model contract
    is exactly 52 active subcarriers of a 64-pair HT40 frame).
    """
    amps: list[np.ndarray] = []
    times: list[float] = []
    for r in records:
        if r.subcarrier_count != 64:
            continue
        i_arr = np.asarray(r.i_samples, dtype=np.float64)
        q_arr = np.asarray(r.q_samples, dtype=np.float64)
        amp = np.sqrt(i_arr**2 + q_arr**2)[ACTIVE_INDICES_64]
        amps.append(amp)
        times.append(r.timestamp_real)
    if not amps:
        return np.empty((0, len(ACTIVE_INDICES_64))), np.empty((0,))
    t = np.asarray(times, dtype=np.float64)
    order = np.argsort(t, kind="stable")
    return np.stack(amps)[order], t[order]


# ---------------------------------------------------------------------------
# CSI preprocessing (exact notebook math)
# ---------------------------------------------------------------------------

def _hampel_time(matrix: np.ndarray) -> np.ndarray:
    """Per-subcarrier Hampel along time (window 7, 3σ MAD, interior only).

    Mirrors ``hampel_filter_time`` from SmokeGuard_v2.ipynb: outliers farther
    than ``n_sigmas * MAD`` from the sliding window median are replaced by
    that median; the first/last ``k`` rows are left untouched.
    """
    num_packets, _ = matrix.shape
    if num_packets < HAMPEL_WINDOW:
        return matrix.copy()
    k = (HAMPEL_WINDOW - 1) // 2
    filtered = matrix.copy()
    windows = np.lib.stride_tricks.sliding_window_view(
        matrix, HAMPEL_WINDOW, axis=0
    )
    medians = np.median(windows, axis=-1)
    mads = np.median(np.abs(windows - medians[..., None]), axis=-1)
    centers = matrix[k:num_packets - k]
    outliers = np.abs(centers - medians) > HAMPEL_N_SIGMAS * mads
    filtered[k:num_packets - k] = np.where(outliers, medians, centers)
    return filtered


def _resample_uniform(matrix: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, float]:
    """Linear-interpolate onto a uniform 420-point grid spanning the window.

    Mirrors ``resample_to_uniform_grid``: timestamps are sorted, duplicate
    timestamps dropped, and ``effective_fs = (N-1) / duration`` drives the
    Butterworth cutoff. Raises ValueError on degenerate windows.
    """
    order = np.argsort(t, kind="stable")
    t = t[order]
    matrix = matrix[order]
    keep = np.concatenate(([True], np.diff(t) > 0))
    t = t[keep]
    matrix = matrix[keep]
    if len(t) < 2:
        raise ValueError("CSI window has fewer than 2 distinct timestamps")
    duration = t[-1] - t[0]
    if duration <= 0:
        raise ValueError("CSI window duration is non-positive")
    grid = np.linspace(t[0], t[-1], N_CSI_POINTS)
    right = np.searchsorted(t, grid, side="right")
    lower = np.clip(right - 1, 0, len(t) - 2)
    weight = (grid - t[lower]) / (t[lower + 1] - t[lower])
    weight = np.clip(weight, 0.0, 1.0)[:, None]
    resampled = (1.0 - weight) * matrix[lower] + weight * matrix[lower + 1]
    effective_fs = (N_CSI_POINTS - 1) / duration
    return resampled, effective_fs


def _butterworth_lowpass(matrix: np.ndarray, fs: float) -> np.ndarray:
    """Zero-phase order-4 Butterworth low-pass at 5 Hz along time (axis 0).

    Mirrors ``butterworth_lowpass`` from the notebook (cutoff scaled against
    each window's own effective fs, so short windows are not over-filtered).
    """
    from scipy.signal import butter, filtfilt

    nyquist = 0.5 * fs
    if matrix.shape[0] < 3 or LOWPASS_HZ <= 0 or LOWPASS_HZ >= 0.99 * nyquist:
        return matrix.copy()
    b, a = butter(
        LOWPASS_ORDER, LOWPASS_HZ / nyquist, btype="low", analog=False
    )
    padlen = min(3 * max(len(a), len(b)), matrix.shape[0] - 1)
    return filtfilt(b, a, matrix, axis=0, padlen=padlen)


def preprocess_csi_window(
    t: np.ndarray,
    amp: np.ndarray,
    background: np.ndarray | None,
) -> np.ndarray:
    """Run the full CSI denoising chain → (420, 52) float32 features.

    Order: Hampel → resample to 420 → Butterworth 5 Hz → Gaussian σ=2 →
    subtract the static background (when available). Raises ValueError on
    degenerate windows (fewer than 2 distinct timestamps).
    """
    amp = _hampel_time(amp)
    resampled, effective_fs = _resample_uniform(amp, t)
    filtered = _butterworth_lowpass(resampled, effective_fs)
    from scipy.ndimage import gaussian_filter1d

    smoothed = gaussian_filter1d(filtered, sigma=GAUSSIAN_SIGMA, axis=0)
    if background is not None:
        smoothed = smoothed - background[None, :]
    return smoothed.astype(np.float32, copy=False)


def capture_background(t: np.ndarray, amp: np.ndarray) -> np.ndarray:
    """Compute the static background (52,) = time-mean of a preprocessed window.

    Matches the notebook: static segments go through the same denoising chain
    as activity windows, then the background is their mean over time.
    """
    return preprocess_csi_window(t, amp, None).mean(axis=0)


# ---------------------------------------------------------------------------
# Sensor feature extraction (exact 22-feature contract)
# ---------------------------------------------------------------------------

def extract_sensor_features(
    window: list[SmokeRecord],
    baseline: list[SmokeRecord],
) -> np.ndarray | None:
    """Build the (22,) feature row for the SVM, or None when BME680 is absent.

    For each channel: mean(window) − median(baseline), std(window, ddof=0),
    last − first; plus gas_kohm__base_ratio. Legacy 11-field firmware has no
    BME680 block — its channels are None and the sensor path is unavailable.
    """
    window_values: dict[str, list[float]] = {}
    baseline_values: dict[str, list[float]] = {}
    for ch in SENSOR_CHANNELS:
        w = [float(getattr(r, ch)) for r in window]
        b = [float(getattr(r, ch)) for r in baseline]
        if any(v is None for v in w) or any(v is None for v in b):
            return None
        window_values[ch] = w
        baseline_values[ch] = b

    features: list[float] = []
    for ch in SENSOR_CHANNELS:
        w = np.asarray(window_values[ch], dtype=np.float64)
        b = np.asarray(baseline_values[ch], dtype=np.float64)
        features.append(float(np.mean(w) - np.median(b)))
        features.append(float(np.std(w, ddof=0)))
        features.append(float(w[-1] - w[0]))
    gas_w = np.asarray(window_values["gas_kohm"], dtype=np.float64)
    gas_b = np.asarray(baseline_values["gas_kohm"], dtype=np.float64)
    features.append(float(np.mean(gas_w) / max(np.median(gas_b), 1e-9)))
    return np.asarray(features, dtype=np.float64)


# ---------------------------------------------------------------------------
# Platt calibrator (schema-2 JSON from SmokeGuard_v2.ipynb)
# ---------------------------------------------------------------------------

def default_platt_path(csi_model_path: str) -> str:
    """Sibling calibrator path (``models/x.keras`` → ``models/x.platt.json``)."""
    return str(Path(csi_model_path).with_suffix(".platt.json"))


def load_platt_calibration(path: str) -> dict:
    """Load the notebook's schema-2 Platt calibrator JSON.

    Returns the multiclass one-vs-rest slopes/intercepts (in ``CSI_CLASSES``
    order) and the dedicated binary smoke ``(slope, intercept)`` pair.  A
    schema, class-list, shape, or finiteness mismatch raises ``ValueError`` so
    the caller can disable the CSI path instead of silently applying the wrong
    calibration.
    """
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if payload.get("schema_version") != PLATT_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported Platt calibrator schema "
            f"{payload.get('schema_version')!r}; expected "
            f"{PLATT_SCHEMA_VERSION}."
        )
    if payload.get("classes") != CSI_CLASSES:
        raise ValueError(
            "Platt calibrator classes do not match the backend CSI class list."
        )
    multiclass = payload.get("multiclass") or {}
    if multiclass.get("classes") != CSI_CLASSES:
        raise ValueError(
            "Multiclass Platt calibrator classes do not match the backend "
            "CSI class list."
        )

    expected = (len(CSI_CLASSES),)
    slopes = np.asarray(multiclass.get("slope_A"), dtype=np.float64)
    intercepts = np.asarray(multiclass.get("intercept_B"), dtype=np.float64)
    if slopes.shape != expected or intercepts.shape != expected:
        raise ValueError(
            f"Platt calibrator shapes {slopes.shape} / {intercepts.shape} "
            f"do not match {expected}."
        )
    if not (np.isfinite(slopes).all() and np.isfinite(intercepts).all()):
        raise ValueError("Platt calibrator contains non-finite values.")

    smoke = payload.get("smoke_binary") or {}
    try:
        smoke_pair = (float(smoke["slope_A"]), float(smoke["intercept_B"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "Platt calibrator is missing a valid smoke_binary pair."
        ) from exc
    if not np.isfinite(smoke_pair).all():
        raise ValueError("Binary smoke Platt pair contains non-finite values.")

    return {"slopes": slopes, "intercepts": intercepts, "smoke": smoke_pair}


# ---------------------------------------------------------------------------
# Model registry (lazy loading; TF import never happens at module import)
# ---------------------------------------------------------------------------

_MODEL_LOCK = threading.Lock()
_state: dict = {"csi": None, "sensor": None, "ready": False}


def models_ready() -> bool:
    """True once both models loaded successfully."""
    return bool(_state["ready"])


def csi_model_ready() -> bool:
    """True when the CSI ResNet-LSTM is loaded."""
    return _state["csi"] is not None


def sensor_model_ready() -> bool:
    """True when the smoke SVM is loaded."""
    return _state["sensor"] is not None


def _validate_sensor_meta(smoke_path: str) -> None:
    """Validate the sibling ``smoke_svm.meta.json`` feature contract.

    The notebook writes this provenance sidecar next to the joblib.  When it
    is present, the class list, feature order, and window/baseline contract
    must match the backend extractor, so a retrain that drops or reorders
    features fails at warmup instead of mispredicting.  A missing sidecar is
    tolerated (older deployments shipped only the joblib) but logged.
    """
    meta_path = Path(smoke_path).with_name("smoke_svm.meta.json")
    if not meta_path.exists():
        logger.warning(
            "Smoke SVM metadata %s not found; skipping the feature-schema "
            "check",
            meta_path,
        )
        return
    with open(meta_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if payload.get("schema_version") != SENSOR_META_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported smoke SVM metadata schema "
            f"{payload.get('schema_version')!r}; expected "
            f"{SENSOR_META_SCHEMA_VERSION}."
        )
    if payload.get("classes") != ["no-smoke", "smoke"]:
        raise ValueError(
            "Smoke SVM metadata classes do not match ['no-smoke', 'smoke']."
        )
    feature_config = payload.get("feature_config") or {}
    if feature_config.get("feature_names") != SENSOR_FEATURE_NAMES:
        raise ValueError(
            "Smoke SVM metadata feature_names do not match the backend "
            "extraction order; retrain or update extract_sensor_features()."
        )
    preprocessing = payload.get("preprocessing") or {}
    expected = (
        ("window_seconds", SENSOR_WINDOW_SECONDS),
        ("baseline_seconds", SENSOR_BASELINE_SECONDS),
        ("min_samples_per_interval", SENSOR_MIN_WINDOW_SAMPLES),
        ("min_baseline_samples", SENSOR_MIN_BASELINE_SAMPLES),
    )
    for key, backend_value in expected:
        meta_value = preprocessing.get(key)
        if meta_value is not None and float(meta_value) != float(backend_value):
            raise ValueError(
                f"Smoke SVM metadata {key}={meta_value} does not match the "
                f"backend value {backend_value}."
            )
    logger.info(
        "Smoke SVM metadata validated (%s; threshold %.6f)",
        meta_path,
        float((payload.get("threshold") or {}).get("value", 0.0)),
    )


def load_models(
    csi_path: str, smoke_path: str, platt_path: str | None = None
) -> None:
    """Load the Keras CSI model, its Platt calibrator, and the smoke SVM.

    Each artifact group is loaded independently and errors are logged without
    raising, so the sensor-only path stays available when TensorFlow is broken
    or the CSI model/calibrator is missing.  When ``platt_path`` is None the
    calibrator is read from the checkpoint's sibling ``.platt.json``.
    """
    with _MODEL_LOCK:
        try:
            _load_csi_model(csi_path, platt_path or default_platt_path(csi_path))
        except Exception:
            logger.exception(
                "Failed to load the CSI ResNet-LSTM model or its Platt "
                "calibrator"
            )
            _state["csi"] = None
        try:
            import joblib

            sensor = joblib.load(smoke_path)
            expected_features = len(SENSOR_FEATURE_NAMES)
            if (
                getattr(sensor, "n_features_in_", expected_features)
                != expected_features
            ):
                raise ValueError(
                    f"Smoke SVM expects {sensor.n_features_in_} features; the "
                    f"backend extracts {expected_features}. Retrain or update "
                    "extract_sensor_features() in sync with the notebook."
                )
            _validate_sensor_meta(smoke_path)
            _state["sensor"] = sensor
        except Exception:
            logger.exception("Failed to load the smoke SVM pipeline")
            _state["sensor"] = None
        _state["ready"] = _state["csi"] is not None and _state["sensor"] is not None
        if _state["ready"]:
            logger.info(
                "ML models loaded (CSI ResNet-LSTM + smoke SVM)"
            )
        elif _state["sensor"] is not None:
            logger.warning(
                "Only the smoke SVM loaded — CSI fusion is unavailable"
            )


_AUGMENT_CLASS = None


def _get_augment_class():
    """Define (once) the training-only augmentation layer used by the archive.

    The model was saved with ``registered_name='smokeguard>TimeSeriesAugment'``;
    Keras looks custom objects up by that registered name, so the class is
    registered under the same package and also passed explicitly by both keys.
    """
    global _AUGMENT_CLASS
    if _AUGMENT_CLASS is not None:
        return _AUGMENT_CLASS

    from keras import layers
    from keras.saving import register_keras_serializable

    @register_keras_serializable(package="smokeguard")
    class TimeSeriesAugment(layers.Layer):
        """Training-only augmentation from the notebook — no-op at inference."""

        def __init__(self, shift_max=12, scale_min=0.9, scale_max=1.1,
                     noise_std=0.05, **kwargs):
            super().__init__(**kwargs)
            self.shift_max = shift_max
            self.scale_min = scale_min
            self.scale_max = scale_max
            self.noise_std = noise_std

        def call(self, inputs, training=False):
            # Training-time shift/scale/noise is irrelevant at inference.
            return inputs

        def get_config(self):
            config = super().get_config()
            config.update(
                shift_max=self.shift_max,
                scale_min=self.scale_min,
                scale_max=self.scale_max,
                noise_std=self.noise_std,
            )
            return config

    _AUGMENT_CLASS = TimeSeriesAugment
    return TimeSeriesAugment


def _load_csi_model(path: str, platt_path: str) -> None:
    """Load the .keras archive, activity weights, and the Platt calibrator.

    The full model is never softmax-predicted; logits are reconstructed
    exactly as the notebooks do: ``dense_features @ kernel + bias``.  The
    model's output width must match ``CSI_CLASSES`` so a stale or misnamed
    checkpoint cannot silently drop a class.
    """
    import tensorflow as tf  # noqa: F401  (import registers keras backend)
    from keras import Model, models

    augment_cls = _get_augment_class()
    model = models.load_model(
        path,
        compile=False,
        custom_objects={
            "TimeSeriesAugment": augment_cls,
            "smokeguard>TimeSeriesAugment": augment_cls,
        },
    )
    feature_model = Model(
        inputs=model.inputs,
        outputs=model.get_layer("dense_features").output,
    )
    kernel, bias = model.get_layer("activity").get_weights()
    expected = (len(CSI_CLASSES),)
    if kernel.shape[-1] != expected[0] or bias.shape != expected:
        raise ValueError(
            f"CSI model emits {kernel.shape[-1]} class(es) but the backend "
            f"expects {expected[0]} ({CSI_CLASSES})."
        )

    calibration = load_platt_calibration(platt_path)
    _state["csi"] = {
        "feature_model": feature_model,
        "kernel": kernel,
        "bias": bias,
        "platt_slopes": calibration["slopes"],
        "platt_intercepts": calibration["intercepts"],
        "smoke_platt": calibration["smoke"],
    }
    logger.info(
        "CSI model loaded (%d classes; calibrator %s)",
        len(CSI_CLASSES),
        platt_path,
    )


def predict_csi(features: np.ndarray) -> CsiResult:
    """ResNet-LSTM logits → multiclass Platt calibration → smoke gate."""
    csi = _state["csi"]
    if csi is None:
        raise RuntimeError("CSI model is not loaded")
    batch = np.asarray(features, dtype=np.float32)[None, ...]
    dense = csi["feature_model"].predict(batch, verbose=0)[0]
    logits = dense @ csi["kernel"] + csi["bias"]  # (7,)

    # One-vs-rest Platt scaling, then renormalize (notebook
    # PlattCalibrator.transform).  The calibrator is class-order aligned.
    calibrated = _stable_sigmoid(
        logits * csi["platt_slopes"] + csi["platt_intercepts"]
    )
    total = float(calibrated.sum())
    if total > 0.0:
        calibrated = calibrated / max(total, 1e-12)
    else:
        calibrated = np.full(len(CSI_CLASSES), 1.0 / len(CSI_CLASSES))

    slope, intercept = csi["smoke_platt"]
    p_smoke = float(
        _stable_sigmoid(logits[CSI_SMOKE_INDEX] * slope + intercept)
    )
    pred_idx = int(np.argmax(calibrated))
    return CsiResult(
        logits=logits,
        calibrated=calibrated,
        p_smoke=p_smoke,
        hit=pred_idx == CSI_SMOKE_INDEX,
        pred_class=CSI_CLASSES[pred_idx],
    )


def predict_csi_from_snapshot(
    payload: dict, background: np.ndarray | None
) -> CsiResult | None:
    """Re-score a stored v2 CSI snapshot (best-effort legacy backfill).

    The snapshot holds amplitude-only frames (rounded to 2 dp) for the active
    subcarriers of the window that produced the original decision. Repeating
    the preprocessing chain reproduces the model input; slight rounding and a
    possibly recaptured static background mean the result is a reconstruction,
    not the original call. Returns None when it cannot be re-scored (pre-fix
    subcarrier mapping, degenerate frames, or the CSI model is not loaded).
    """
    if payload.get("snapshot_version") != 2:
        return None
    if _state["csi"] is None:
        return None
    frames = payload.get("frames") or []
    if len(frames) < 2:
        return None
    try:
        t = np.asarray([float(frame["t"]) for frame in frames], dtype=np.float64)
        amp = np.asarray([frame["amp"] for frame in frames], dtype=np.float64)
    except (KeyError, TypeError, ValueError):
        return None
    try:
        features = preprocess_csi_window(t, amp, background)
    except (TypeError, ValueError):
        return None
    return predict_csi(features)


def _stable_sigmoid(x: np.ndarray) -> np.ndarray:
    """Overflow-safe logistic function (Platt slopes can be large)."""
    x = np.asarray(x, dtype=np.float64)
    out = np.empty_like(x)
    pos = x >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    exp_x = np.exp(x[~pos])
    out[~pos] = exp_x / (1.0 + exp_x)
    return out


def predict_sensor(features: np.ndarray) -> float:
    """SVM P(smoke) for one (22,) feature row."""
    sensor = _state["sensor"]
    if sensor is None:
        raise RuntimeError("Smoke SVM is not loaded")
    return float(sensor.predict_proba(features.reshape(1, -1))[0, 1])


# ---------------------------------------------------------------------------
# Multi-scale CSI window selection
# ---------------------------------------------------------------------------

def csi_window_candidates(
    min_seconds: float, max_seconds: float, step_seconds: float
) -> list[float]:
    """Candidate window durations (inclusive), stepped with integer indices.

    Integer stepping avoids float drift from repeated ``+= step``.
    """
    if step_seconds <= 0 or max_seconds < min_seconds:
        return []
    steps = int(np.floor((max_seconds - min_seconds) / step_seconds + 1e-9))
    candidates = [
        round(min_seconds + k * step_seconds, 6) for k in range(steps + 1)
    ]
    if candidates and max_seconds - candidates[-1] > 1e-9:
        candidates.append(round(max_seconds, 6))
    return candidates


def select_best_csi_candidate(
    candidates: list[tuple[float, CsiResult]],
) -> tuple[float, CsiResult] | None:
    """Pick the winning (duration, result).

    Lexicographic max of ``(hit, p_smoke)``: a window whose argmax is
    ``smoke`` dominates any window without that gate; then higher binary
    P(smoke).  Strict ``>`` keeps the shortest duration on ties.
    """
    best: tuple[float, CsiResult] | None = None
    for duration, result in candidates:
        key = (result.hit, result.p_smoke)
        if best is None:
            best = (duration, result)
            continue
        best_key = (best[1].hit, best[1].p_smoke)
        if key > best_key:
            best = (duration, result)
    return best


# ---------------------------------------------------------------------------
# Late fusion
# ---------------------------------------------------------------------------

def fuse(
    csi: CsiResult | None,
    p_sensor: float | None,
    csi_weight: float,
    score_threshold: float,
    sensor_threshold: float = SENSOR_THRESHOLD,
    csi_window_seconds: float | None = None,
    *,
    csi_only_enabled: bool = False,
    csi_only_min_prob: float = 0.0,
) -> FusionDecision | None:
    """Apply the late-fusion policy for one evaluation tick.

    The notebook's approved policy is a conjunction (CSI argmax gate AND
    sensor gate AND fused score ≥ threshold). Per the product requirement,
    a sensor-only hit still alarms and is labeled ``smoke_only``.

    Experimental: with ``csi_only_enabled``, a CSI smoking-gesture hit with
    the sensor *operating but below its gate* yields ``csi_only`` — the
    weakest class (no fusion score).  A missing sensor reading (``None``)
    never produces it: that is "sensor unavailable", not "sensor said no".
    """
    sensor_hit = p_sensor is not None and p_sensor >= sensor_threshold
    if not sensor_hit:
        if (
            csi_only_enabled
            and p_sensor is not None
            and csi is not None
            and csi.hit
            and csi.p_smoke >= csi_only_min_prob
        ):
            return FusionDecision(
                label="csi_only",
                p_csi=csi.p_smoke,
                p_sensor=p_sensor,
                fused_score=None,
                csi_hit=True,
                sensor_hit=False,
                csi_pred_class=csi.pred_class,
                calibrated_probs=csi.calibrated_probs,
                csi_window_seconds=csi_window_seconds,
            )
        return None

    csi_hit = csi is not None and csi.hit
    p_csi_val = csi.p_smoke if csi is not None else None
    fused_score = (
        csi_weight * (p_csi_val if p_csi_val is not None else 0.0)
        + (1.0 - csi_weight) * (p_sensor if p_sensor is not None else 0.0)
    )

    if csi_hit and fused_score >= score_threshold:
        return FusionDecision(
            label="fusion",
            p_csi=p_csi_val,
            p_sensor=p_sensor,
            fused_score=fused_score,
            csi_hit=True,
            sensor_hit=True,
            csi_pred_class=csi.pred_class if csi else None,
            calibrated_probs=csi.calibrated_probs if csi else {},
            csi_window_seconds=csi_window_seconds,
        )
    return FusionDecision(
        label="smoke_only",
        p_csi=p_csi_val,
        p_sensor=p_sensor,
        fused_score=fused_score,
        csi_hit=csi_hit,
        sensor_hit=True,
        csi_pred_class=csi.pred_class if csi else None,
        calibrated_probs=csi.calibrated_probs if csi else {},
        csi_window_seconds=csi_window_seconds,
    )
