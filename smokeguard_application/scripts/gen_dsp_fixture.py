"""Generate a verification fixture for the TypeScript DSP port.

Runs the notebook's "Pipeline for visualizing CSI" verbatim (CSI_RESEARCH.ipynb
cell 20: Hampel → Butterworth 20 Hz filtfilt → Gaussian sigma=2) on a seeded
random matrix with injected outliers and writes inputs/outputs to
/tmp/dsp_fixture.json, so frontend/src/dsp/preprocess.ts can be checked
against scipy (max abs diff <= 1e-5).

Usage:
    uv run --with scipy python scripts/gen_dsp_fixture.py
    cd frontend && npx esbuild src/dsp/preprocess.ts --bundle --format=esm --outfile=/tmp/preprocess.mjs
    node /tmp/compare_dsp.mjs
"""

import json

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import butter, filtfilt


# Verbatim from the notebook (cell 10)
def hampel_filter(data, window_size=7, n_sigmas=3):
    n = len(data)
    if n < window_size:
        return np.copy(data)
    if window_size % 2 == 0:
        raise ValueError("window_size must be odd")
    k = (window_size - 1) // 2
    filtered_data = np.copy(data)
    for i in range(k, n - k):
        window = data[i - k : i + k + 1]
        median = np.median(window)
        mad = np.median(np.abs(window - median))
        if mad == 0:
            threshold = 0
        else:
            threshold = n_sigmas * mad
        if np.abs(data[i] - median) > threshold:
            filtered_data[i] = median
    return filtered_data


# Verbatim from the notebook (cell 16)
def apply_butterworth_filter(data, cutoff_freq, fs, order=4):
    nyquist = 0.5 * fs
    normal_cutoff = cutoff_freq / nyquist
    b, a = butter(order, normal_cutoff, btype="low", analog=False)
    filtered_data = filtfilt(b, a, data)
    return filtered_data


def main() -> None:
    rng = np.random.default_rng(42)
    # 200 frames x 52 active subcarriers, amplitude-like values (O(10-100))
    amp = rng.normal(30.0, 8.0, size=(200, 52))
    amp += 10.0 * np.sin(np.linspace(0, 4 * np.pi, 200))[:, None]
    # Inject sparse outliers so the Hampel stage has something to remove
    for _ in range(30):
        amp[rng.integers(0, 200), rng.integers(0, 52)] = rng.uniform(200, 400)

    # Pipeline for visualizing CSI (cell 20)
    ns_csi_matrix = np.vstack([hampel_filter(row) for row in amp])
    filtered = np.zeros_like(ns_csi_matrix)
    for i in range(ns_csi_matrix.shape[1]):
        filtered[:, i] = apply_butterworth_filter(ns_csi_matrix[:, i], cutoff_freq=20, fs=100.0)
    gaussian = np.zeros_like(filtered)
    for i in range(filtered.shape[1]):
        gaussian[:, i] = gaussian_filter1d(filtered[:, i], sigma=2)

    with open("/tmp/dsp_fixture.json", "w") as f:
        json.dump({"input": amp.tolist(), "output": gaussian.tolist()}, f)
    print("wrote /tmp/dsp_fixture.json (200 frames x 52 subcarriers)")


if __name__ == "__main__":
    main()
