/**
 * CSI preprocessing — exact TypeScript port of the notebook pipeline
 * (CSI_RESEARCH.ipynb cell 20, "Pipeline for visualizing CSI"):
 *
 *   1. Hampel filter (row-wise, across subcarriers): window 7, 3·MAD
 *      threshold, median replacement, interior points only.
 *   2. Butterworth low-pass (column-wise, along time): 4th order,
 *      20 Hz @ fs 100 Hz, zero-phase (scipy filtfilt semantics).
 *   3. Gaussian smoothing (column-wise): sigma = 2, scipy
 *      gaussian_filter1d defaults (mode='reflect', truncate=4.0).
 *
 * Verified against scipy on a seeded fixture (max abs diff ≤ 1e-5);
 * see scripts/gen_dsp_fixture.py.
 */

// ---------------------------------------------------------------------------
// 1. Hampel filter (notebook cell 10, literal port)
// ---------------------------------------------------------------------------

export function hampelRow(row: number[], windowSize = 7, nSigmas = 3): number[] {
  const n = row.length;
  if (n < windowSize) return row.slice();
  if (windowSize % 2 === 0) throw new Error('windowSize must be odd');
  const k = Math.floor((windowSize - 1) / 2);
  const out = row.slice();
  const win = new Array<number>(windowSize);
  const sorted = new Array<number>(windowSize);
  const devs = new Array<number>(windowSize);
  for (let i = k; i < n - k; i++) {
    for (let j = -k; j <= k; j++) win[j + k] = row[i + j];
    for (let j = 0; j < windowSize; j++) sorted[j] = win[j];
    sorted.sort((a, b) => a - b);
    const median = sorted[k];
    for (let j = 0; j < windowSize; j++) devs[j] = Math.abs(win[j] - median);
    devs.sort((a, b) => a - b);
    const mad = devs[k];
    // Notebook quirk preserved: no 1.4826 scale, MAD == 0 → threshold 0
    const threshold = mad === 0 ? 0 : nSigmas * mad;
    if (Math.abs(row[i] - median) > threshold) out[i] = median;
  }
  return out;
}

// ---------------------------------------------------------------------------
// 2. Butterworth low-pass 4th order, 20 Hz @ fs 100 Hz (zero-phase)
// ---------------------------------------------------------------------------

// SOS sections from scipy.signal.butter(4, 20, fs=100, output='sos'); each
// row is [b0, b1, b2, a1, a2] (a0 = 1). Steady-state initial conditions
// from scipy.signal.sosfilt_zi(sos). Both generated once via scipy — see
// the plan notes; identical to the notebook's filtfilt(b, a, x) path.
const BUTTER_SOS: number[][] = [
  [0.046582906636443676, 0.09316581327288735, 0.046582906636443676, -0.32897567737095285, 0.06458765491644297],
  [1.0, 2.0, 1.0, -0.4531195206523847, 0.4663255707632367],
];

const BUTTER_ZI: number[][] = [
  [0.20671860589126934, 0.030222755955490694],
  [0.746698487472287, -0.21302405823552373],
];

/** scipy edge = 3 * (2 * n_sections + 1). */
const PADLEN = 3 * (2 * BUTTER_SOS.length + 1); // 15

/** One biquad section (direct form II transposed), scipy sosfilt zi form. */
function sosFilterSection(
  x: number[],
  b: [number, number, number],
  a1: number,
  a2: number,
  zi: [number, number],
): number[] {
  const n = x.length;
  const y = new Array<number>(n);
  y[0] = b[0] * x[0] + zi[0];
  if (n > 1) y[1] = b[0] * x[1] + b[1] * x[0] + zi[1] - a1 * y[0];
  for (let i = 2; i < n; i++) {
    y[i] = b[0] * x[i] + b[1] * x[i - 1] + b[2] * x[i - 2] - a1 * y[i - 1] - a2 * y[i - 2];
  }
  return y;
}

/** All sections in order; zi scaled by the first sample (scipy sosfiltfilt). */
function sosFilter(x: number[], ziScale: number): number[] {
  let y = x;
  for (let s = 0; s < BUTTER_SOS.length; s++) {
    const sec = BUTTER_SOS[s];
    y = sosFilterSection(
      y,
      [sec[0], sec[1], sec[2]],
      sec[3],
      sec[4],
      [BUTTER_ZI[s][0] * ziScale, BUTTER_ZI[s][1] * ziScale],
    );
  }
  return y;
}

/** Zero-phase Butterworth (scipy filtfilt): pad → forward → reverse → forward → reverse. */
export function filtfiltSos(x: number[]): number[] {
  const n = x.length;
  if (n <= PADLEN) return x.slice(); // scipy raises; short snapshots degrade gracefully

  // Odd reflection padding (scipy _odd_ext): left = 2*x[0] - x[15..1]
  const padded = new Array<number>(2 * PADLEN + n);
  for (let j = 0; j < PADLEN; j++) padded[j] = 2 * x[0] - x[PADLEN - j];
  for (let i = 0; i < n; i++) padded[PADLEN + i] = x[i];
  for (let j = 0; j < PADLEN; j++) padded[PADLEN + n + j] = 2 * x[n - 1] - x[n - 2 - j];

  const fwd = sosFilter(padded, padded[0]);
  const rev = fwd.slice().reverse();
  const fwd2 = sosFilter(rev, rev[0]);
  const out = fwd2.slice().reverse();
  return out.slice(PADLEN, PADLEN + n);
}

// ---------------------------------------------------------------------------
// 3. Gaussian smoothing (scipy gaussian_filter1d: mode='reflect', truncate=4)
// ---------------------------------------------------------------------------

export function gaussianSmooth(x: number[], sigma = 2): number[] {
  const radius = Math.floor(4.0 * sigma + 0.5); // 8
  const kernel = new Array<number>(2 * radius + 1);
  let sum = 0;
  for (let k = -radius; k <= radius; k++) {
    const v = Math.exp(-0.5 * (k / sigma) * (k / sigma));
    kernel[k + radius] = v;
    sum += v;
  }
  for (let i = 0; i < kernel.length; i++) kernel[i] /= sum;

  const n = x.length;
  const out = new Array<number>(n);
  for (let i = 0; i < n; i++) {
    let acc = 0;
    for (let k = -radius; k <= radius; k++) {
      let idx = i + k;
      // ndimage reflect-mirror semantics (probed empirically): edge value is
      // duplicated — m(-1) = 0, m(n) = n-1. Single fold suffices for n >= 8;
      // the loop keeps pathological tiny inputs (n < 8) from going negative.
      while (idx < 0 || idx >= n) {
        if (idx < 0) idx = -idx - 1;
        else idx = 2 * n - 1 - idx;
      }
      acc += kernel[k + radius] * x[idx];
    }
    out[i] = acc;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Full pipeline (frame-major in → frame-major out)
// ---------------------------------------------------------------------------

export function preprocessMatrix(amp: number[][]): number[][] {
  const frames = amp.length;
  if (frames === 0) return [];
  const nActive = amp[0].length;

  // 1. Hampel per frame row (across subcarriers)
  const h = amp.map(row => hampelRow(row));

  // 2+3. Butterworth + Gaussian per subcarrier column (along time)
  const out: number[][] = Array.from({ length: frames }, () => new Array<number>(nActive));
  const col = new Array<number>(frames);
  for (let sc = 0; sc < nActive; sc++) {
    for (let f = 0; f < frames; f++) col[f] = h[f][sc];
    const sm = gaussianSmooth(filtfiltSos(col));
    for (let f = 0; f < frames; f++) out[f][sc] = sm[f];
  }
  return out;
}

// ---------------------------------------------------------------------------
// Subcarrier picking
// ---------------------------------------------------------------------------

/** Pick `count` distinct random entries from `candidates` (sorted output). */
export function pickRandomSubcarriers(
  candidates: readonly number[],
  count = 3,
  rng: () => number = Math.random,
): number[] {
  const idx = candidates.slice();
  const n = idx.length;
  const c = Math.min(count, n);
  for (let i = 0; i < c; i++) {
    const j = i + Math.floor(rng() * (n - i));
    const tmp = idx[i];
    idx[i] = idx[j];
    idx[j] = tmp;
  }
  return idx.slice(0, c).sort((a, b) => a - b);
}
