/**
 * Model Info page data — a static snapshot of the training/evaluation results.
 *
 * Sources (extracted 2026-11-18), at these paths in the GitHub repository:
 * - notebooks/SmokeGuard_v2.ipynb — CSI 7-class ResNet-LSTM
 * - notebooks/SmokeGuard_Smoke_Sensor.ipynb — air-quality SVM
 * - notebooks/SmokeGuard_Fusion.ipynb — late-fusion policy + simulation
 * - smokeguard_application/models/csi_resnet_lstm_7class.platt.json — CSI
 *   calibration diagnostics
 * - smokeguard_application/models/smoke_svm.meta.json — sensor
 *   holdout/OOF/CV metrics
 * - smokeguard_application/app/config.py — deployed thresholds/weights
 *
 * The Provenance section links these paths on the repository's `main` branch.
 *
 * Nothing here is fetched at runtime. Re-extract these values and bump
 * SNAPSHOT_DATE whenever a model is retrained. Components format numbers;
 * they never own the metrics.
 */

export const SNAPSHOT_DATE = '2026-11-18';

export const CSI_CLASSES = [
  'smoke',
  'eat',
  'drink',
  'sit',
  'stand',
  'random',
  'static',
] as const;

export type CsiClassName = (typeof CSI_CLASSES)[number];

export interface ConfusionData {
  rowLabels: readonly string[];
  colLabels: readonly string[];
  /** counts[row][col]: rows are actual, columns are predicted. */
  counts: readonly (readonly number[])[];
}

export interface SummaryMetrics {
  n: number;
  accuracy: number;
  balancedAccuracy: number;
  macroF1: number;
  brier?: number;
  ece?: number;
  logLoss?: number;
}

export interface PerClassMetric {
  className: string;
  precision: number;
  recall: number;
  f1: number;
  support: number;
}

export interface EvaluationView {
  key: string;
  label: string;
  split: string;
  summary: SummaryMetrics;
  perClass: readonly PerClassMetric[];
  confusion: ConfusionData;
}

export interface ValidationRow {
  key: string;
  label: string;
  n: number;
  accuracy: number;
  macroF1: number;
}

export interface BinaryMetrics {
  n: number;
  accuracy: number;
  balancedAccuracy: number;
  precision: number;
  recall: number;
  f1: number;
  rocAuc?: number;
  averagePrecision?: number;
  brier?: number;
}

export interface ModelFact {
  label: string;
  value: string;
}

export interface MetricDefinition {
  term: string;
  definition: string;
}

export interface MetricGroup {
  title: string;
  items: readonly MetricDefinition[];
}

export type FaqCategoryId = 'metrics' | 'fusion' | 'limits';

export interface FaqCategory {
  id: FaqCategoryId;
  title: string;
}

export interface FaqItem {
  /** Stable slug: React key and open-state key. */
  id: string;
  category: FaqCategoryId;
  question: string;
  answer: string;
}

// ---------------------------------------------------------------------------
// CSI ResNet-LSTM — 7-class gesture model
// ---------------------------------------------------------------------------

export const CSI_MODEL = {
  name: 'Wi-Fi CSI gesture model',
  architecture: 'ResNet-LSTM',
  role:
    'Classifies a short Wi-Fi CSI gesture window into smoke, eat, drink, sit, '
    + 'stand, random, or static.',
  facts: [
    { label: 'Classes', value: '7' },
    { label: 'Input', value: '420 × 52' },
    { label: 'Parameters', value: '216,615' },
    { label: 'LSTM units', value: '96' },
    { label: 'Filters', value: '32 / 64 / 128' },
    { label: 'Window scan', value: '4.0–7.0 s' },
    { label: 'Train / val / test', value: '1,628 / 224 / 224' },
    { label: 'Trained', value: '2026-10-30' },
  ] satisfies readonly ModelFact[],
} as const;

const CSI_RAW_PER_CLASS: readonly PerClassMetric[] = [
  { className: 'smoke', precision: 0.8333333, recall: 0.78125, f1: 0.8064516, support: 32 },
  { className: 'eat', precision: 0.8285714, recall: 0.90625, f1: 0.8656716, support: 32 },
  { className: 'drink', precision: 0.7241379, recall: 0.65625, f1: 0.6885246, support: 32 },
  { className: 'sit', precision: 0.9393939, recall: 0.96875, f1: 0.9538462, support: 32 },
  { className: 'stand', precision: 1.0, recall: 1.0, f1: 1.0, support: 32 },
  { className: 'random', precision: 0.7428571, recall: 0.8125, f1: 0.7761194, support: 32 },
  { className: 'static', precision: 0.9666667, recall: 0.90625, f1: 0.9354839, support: 32 },
];

const CSI_CALIBRATED_PER_CLASS: readonly PerClassMetric[] = [
  { className: 'smoke', precision: 0.78125, recall: 0.78125, f1: 0.78125, support: 32 },
  { className: 'eat', precision: 0.8055556, recall: 0.90625, f1: 0.8529412, support: 32 },
  { className: 'drink', precision: 0.7407407, recall: 0.625, f1: 0.6779661, support: 32 },
  { className: 'sit', precision: 0.96875, recall: 0.96875, f1: 0.96875, support: 32 },
  { className: 'stand', precision: 1.0, recall: 1.0, f1: 1.0, support: 32 },
  { className: 'random', precision: 0.7352941, recall: 0.78125, f1: 0.7575758, support: 32 },
  { className: 'static', precision: 0.9677419, recall: 0.9375, f1: 0.952381, support: 32 },
];

// Rows = actual, columns = predicted, in CSI_CLASSES order.
const CSI_RAW_CONFUSION: ConfusionData = {
  rowLabels: CSI_CLASSES,
  colLabels: CSI_CLASSES,
  counts: [
    [25, 0, 4, 0, 0, 3, 0],
    [0, 29, 1, 1, 0, 1, 0],
    [3, 5, 21, 0, 0, 3, 0],
    [0, 0, 0, 31, 0, 1, 0],
    [0, 0, 0, 0, 32, 0, 0],
    [1, 1, 3, 0, 0, 26, 1],
    [1, 0, 0, 1, 0, 1, 29],
  ],
};

const CSI_CALIBRATED_CONFUSION: ConfusionData = {
  rowLabels: CSI_CLASSES,
  colLabels: CSI_CLASSES,
  counts: [
    [25, 1, 3, 0, 0, 3, 0],
    [0, 29, 1, 1, 0, 1, 0],
    [4, 5, 20, 0, 0, 3, 0],
    [0, 0, 0, 31, 0, 1, 0],
    [0, 0, 0, 0, 32, 0, 0],
    [2, 1, 3, 0, 0, 25, 1],
    [1, 0, 0, 0, 0, 1, 30],
  ],
};

export const CSI_TEST_CALIBRATED: EvaluationView = {
  key: 'test-calibrated',
  label: 'Platt-calibrated argmax · held-out test',
  split: 'Held-out test · 224 windows · 32 per class',
  summary: {
    n: 224,
    accuracy: 0.8571428571428571,
    balancedAccuracy: 0.8571428571428571,
    macroF1: 0.8558377125888877,
    brier: 0.19917111691459796,
    ece: 0.06438588440976434,
    logLoss: 0.4433859671845802,
  },
  perClass: CSI_CALIBRATED_PER_CLASS,
  confusion: CSI_CALIBRATED_CONFUSION,
};

export const CSI_TEST_RAW: EvaluationView = {
  key: 'test-raw',
  label: 'Raw softmax argmax · held-out test (notebook headline)',
  split: 'Held-out test · 224 windows · 32 per class',
  summary: {
    n: 224,
    accuracy: 0.8616071428571429,
    balancedAccuracy: 0.8616071428571429,
    macroF1: 0.860871038951025,
    brier: 0.21302612799278758,
    ece: 0.04547590176974018,
    logLoss: 0.4506044216714746,
  },
  perClass: CSI_RAW_PER_CLASS,
  confusion: CSI_RAW_CONFUSION,
};

/** Validation aggregate rows only — no validation per-class table/matrix exists. */
export const CSI_VALIDATION_ROWS: readonly ValidationRow[] = [
  { key: 'val-raw', label: 'Validation · raw softmax', n: 224, accuracy: 0.8883928571428571, macroF1: 0.890622149589541 },
  { key: 'val-cal', label: 'Validation · Platt-calibrated', n: 224, accuracy: 0.875, macroF1: 0.8753800506968712 },
];

/** Binary smoke diagnostic at the notebook's session-quantile threshold. */
export const CSI_BINARY_TEST = {
  label: 'Binary smoke view · held-out test',
  threshold: 0.3259,
  metrics: {
    n: 224,
    accuracy: 0.9375,
    balancedAccuracy: 0.8723958333,
    precision: 0.78125,
    recall: 0.78125,
    f1: 0.78125,
    rocAuc: 0.95263671875,
    averagePrecision: 0.8334992164,
  } satisfies BinaryMetrics,
  confusion: {
    rowLabels: ['no-smoke', 'smoke'],
    colLabels: ['no-smoke', 'smoke'],
    counts: [
      [185, 7],
      [7, 25],
    ],
  } satisfies ConfusionData,
};

/** Unseen-session domain-shift probe — caveat, not a headline result. */
export const CSI_UNSEEN = {
  label: 'Unseen session 2026-10-31',
  metrics: {
    n: 350,
    accuracy: 0.3057,
    balancedAccuracy: 0.3057,
    macroF1: 0.2048,
  } satisfies SummaryMetrics,
  smoke: { precision: 0.4353, recall: 0.74, f1: 0.5481 },
  binary: {
    metrics: {
      n: 350,
      accuracy: 0.8628571429,
      balancedAccuracy: 0.72,
      precision: 0.52,
      recall: 0.52,
      f1: 0.52,
      rocAuc: 0.816,
      averagePrecision: 0.5601,
    } satisfies BinaryMetrics,
    confusion: {
      rowLabels: ['no-smoke', 'smoke'],
      colLabels: ['no-smoke', 'smoke'],
      counts: [
        [276, 24],
        [24, 26],
      ],
    } satisfies ConfusionData,
  },
};

// ---------------------------------------------------------------------------
// Smoke-sensor SVM — binary no-smoke / smoke
// ---------------------------------------------------------------------------

export const SENSOR_MODEL = {
  name: 'Air-quality smoke model',
  architecture: 'RBF SVM',
  role:
    'Reads a 14.9 s window of PMS5003 and BME680 channels against a 600 s '
    + 'causal gas baseline and estimates whether the air looks smoky.',
  facts: [
    { label: 'Classes', value: 'no-smoke / smoke' },
    { label: 'Features', value: '22' },
    { label: 'Channels', value: 'temp, humidity, pressure, gas, PM1/2.5/10' },
    { label: 'Window / baseline', value: '14.9 s / 600 s' },
    { label: 'Train / test', value: '598 / 108' },
    { label: 'Kernel / C', value: 'RBF / 0.75' },
    { label: 'Gate threshold', value: '0.4050' },
    { label: 'Trained', value: '2026-11-08' },
  ] satisfies readonly ModelFact[],
} as const;

export const SENSOR_TEST = {
  label: 'File holdout (TEST_DATA.csv)',
  metrics: {
    n: 108,
    accuracy: 0.9814814814814815,
    balancedAccuracy: 0.9863013698630136,
    precision: 0.9459459459459459,
    recall: 1.0,
    f1: 0.9722222222222222,
    rocAuc: 1.0,
    averagePrecision: 1.0,
    brier: 0.00430142757642526,
  } satisfies BinaryMetrics,
  confusion: {
    rowLabels: ['no-smoke', 'smoke'],
    colLabels: ['no-smoke', 'smoke'],
    counts: [
      [71, 2],
      [0, 35],
    ],
  } satisfies ConfusionData,
};

export const SENSOR_OOF = {
  f1: 0.9688149688149689,
  precision: 0.9471544715447154,
  recall: 0.9914893617021276,
};

export const SENSOR_CV = {
  folds: 5,
  meanF1: 0.9612734316600541,
  stdF1: 0.026652608068599842,
};

// ---------------------------------------------------------------------------
// Late fusion — deployed policy + simulated evaluation
// ---------------------------------------------------------------------------

export const FUSION = {
  policy: {
    weightCsi: 0.5,
    weightSensor: 0.5,
    csiGate: 'argmax(calibrated 7-class) == "smoke"',
    sensorGate: 0.4049989698494056,
    fusedThreshold: 0.7,
    formula: 'score = 0.50 × P_CSI(smoke) + 0.50 × P_sensor(smoke)',
    labels: [
      { label: 'fusion', text: 'CSI and sensor both confirm, and the fused score reaches 0.70.' },
      { label: 'smoke_only', text: 'The sensor gate passes but CSI/fusion does not; a possible alert.' },
      { label: 'csi_only', text: 'Experimental, off by default: a CSI smoke gesture while the sensor stays below its gate.' },
    ],
  },
  simulation: {
    csiWindows: 224,
    sensorIntervals: 108,
    pairs: 24192,
    positives: 1120,
    negatives: 23072,
    prevalence: 0.046296296296296294,
    tp: 840,
    fp: 175,
    fn: 280,
    tn: 22897,
    tpr: 0.75,
    fpr: 0.00758495145631068,
    youdenJ: 0.7424150485436893,
    precision: 0.8275862068965517,
    recall: 0.75,
    f1: 0.7868852459016394,
    balancedAccuracy: 0.8712075242718447,
    simulatedAccuracy: 0.9811921296,
    confusion: {
      rowLabels: ['smoking', 'no-smoking'],
      colLabels: ['alert', 'no alert'],
      counts: [
        [840, 280],
        [175, 22897],
      ],
    } satisfies ConfusionData,
    byClass: [
      { className: 'smoke', alerts: 840, pairs: 1120 },
      { className: 'eat', alerts: 0, pairs: 1120 },
      { className: 'drink', alerts: 105, pairs: 1120 },
      { className: 'sit', alerts: 0, pairs: 1120 },
      { className: 'stand', alerts: 0, pairs: 1120 },
      { className: 'random', alerts: 35, pairs: 1120 },
      { className: 'static', alerts: 35, pairs: 1120 },
    ],
  },
};

// ---------------------------------------------------------------------------
// Plain-language metric guide
// ---------------------------------------------------------------------------

export const METRIC_GROUPS: readonly MetricGroup[] = [
  {
    title: 'Reading classifier results',
    items: [
      {
        term: 'Accuracy',
        definition:
          'The share of all predictions that were correct. Easy to read, but it can look high when one class dominates the test set.',
      },
      {
        term: 'Balanced accuracy',
        definition:
          'The average of the per-class recalls. Every class counts equally, so a large majority class cannot hide poor performance on a small one. Chance for seven balanced classes is about 14.3%.',
      },
      {
        term: 'Precision',
        definition:
          'Of the cases the model labelled as a class (or flagged as an alert), the share that really were that class. High precision means few false alarms.',
      },
      {
        term: 'Recall (sensitivity, TPR)',
        definition:
          'Of the cases that really were a class, the share the model found. High recall means few misses.',
      },
      {
        term: 'F1',
        definition:
          'The harmonic mean of precision and recall: one number that balances false alarms and misses. It ignores true negatives, so read it with support and the confusion matrix.',
      },
      {
        term: 'Support',
        definition:
          'How many true examples of a class were in the test set. The CSI test set has 32 windows per class.',
      },
      {
        term: 'Confusion matrix',
        definition:
          'A table of actual class (rows) against predicted class (columns). The bold diagonal is correct; off-diagonal cells show which classes get confused. Row totals are support.',
      },
    ],
  },
  {
    title: 'Ranking and probability quality',
    items: [
      {
        term: 'ROC-AUC',
        definition:
          'The chance that a random positive is ranked above a random negative. 0.5 is random, 1.0 is perfect, and it does not depend on a threshold.',
      },
      {
        term: 'Average precision (AP)',
        definition:
          'A summary of the precision-recall curve. It is more informative than ROC-AUC when positives are rare; compare it with the positive prevalence, not with 0.5.',
      },
      {
        term: 'Brier score',
        definition:
          'The mean squared error of predicted probabilities. Lower is better; it rewards confident correct predictions and penalizes confident mistakes.',
      },
      {
        term: 'ECE (expected calibration error)',
        definition:
          'The average gap between predicted confidence and observed accuracy across bins. 0.05 means the model is about 5 percentage points off on average; lower is better.',
      },
      {
        term: 'Calibration / Platt scaling',
        definition:
          'A rescaling of model scores so that a predicted 70% means roughly 70% observed. Here it is fitted on validation data: one-vs-rest for the seven classes, plus a dedicated binary smoke pair.',
      },
    ],
  },
  {
    title: 'Decisions and protocols',
    items: [
      {
        term: 'Threshold / gate',
        definition:
          'The cut-off for calling a case positive. The sensor gate is 0.405, the fused score must reach 0.70, and the CSI gate is the calibrated argmax class. Lower thresholds usually raise recall and false alarms.',
      },
      {
        term: 'TPR / FPR',
        definition:
          'TPR is recall on real positives (the share caught). FPR is the share of real negatives that incorrectly alert. A good detector has high TPR and low FPR.',
      },
      {
        term: 'Youden J',
        definition:
          'TPR minus FPR. 0 is chance and 1 is perfect; it summarizes one operating point in a single number.',
      },
      {
        term: 'Train / validation / test',
        definition:
          'Training fits the weights; validation chooses calibration and thresholds; test is untouched until the final report. The CSI test split is diagnostics-only because validation fitted the calibrator.',
      },
      {
        term: 'Within-session split',
        definition:
          'The CSI test windows come from the same recording sessions as training. This measures within-session recognition and is optimistic for a new room or person.',
      },
      {
        term: 'File holdout',
        definition:
          'The sensor test set is a whole held-out recording file, so it tests cross-recording generalization — but it is still only one recording.',
      },
      {
        term: 'OOF / cross-validation',
        definition:
          'Out-of-fold predictions come from models that never saw the sample during training. Grouped folds keep all intervals from a recording together, preventing leakage. The sensor threshold and CV score use this.',
      },
      {
        term: 'Late fusion',
        definition:
          'Combining the outputs of independent models rather than their raw signals. Here the calibrated CSI smoke probability and the sensor SVM probability are averaged, then gates decide the alert.',
      },
      {
        term: 'Simulated cross-product',
        definition:
          'Pairing every CSI test window with every sensor test interval. It creates many artificial pairs and a synthetic prevalence; it is not a synchronized real-world test set.',
      },
      {
        term: 'Domain shift',
        definition:
          'Performance change when the environment, person, day, or device changes. The CSI unseen-session check is a domain-shift probe.',
      },
    ],
  },
];

// ---------------------------------------------------------------------------
// FAQ
// ---------------------------------------------------------------------------

export const FAQ_CATEGORIES: readonly FaqCategory[] = [
  { id: 'metrics', title: 'Metrics & calibration' },
  { id: 'fusion', title: 'Fusion & alerts' },
  { id: 'limits', title: 'Limits & provenance' },
];

export const FAQ_ITEMS: readonly FaqItem[] = [
  {
    id: 'single-accuracy',
    category: 'metrics',
    question: 'Why is there no single accuracy number?',
    answer:
      'The three parts are evaluated on different data and units: CSI on 224 test windows, the sensor on 108 held-out intervals, and fusion on 24,192 artificial CSI × sensor pairs. Their percentages answer different questions and are not directly comparable.',
  },
  {
    id: 'fusion-tpr-provenance',
    category: 'fusion',
    question: 'Is the 75% fusion TPR real paired data?',
    answer:
      'No. No synchronized CSI + sensor test recording exists. The fusion evaluation pairs every CSI test window with every sensor test interval (224 × 108 = 24,192 artificial pairs). TPR, FPR, Youden J, and the per-class rates are the interpretable results. Precision, F1, and accuracy depend on the artificial 4.6% positive prevalence.',
  },
  {
    id: 'product-alert-rate',
    category: 'fusion',
    question: 'Does the product alert at the fusion rate?',
    answer:
      'No. The paper tables count only confirmed fusion alerts. The deployed app also emits smoke_only when the sensor gate passes but the CSI/fusion gates do not, so the product’s total alert sensitivity is higher (with more false alarms) than the fusion-only TPR shown here.',
  },
  {
    id: 'sensor-perfect-scores',
    category: 'metrics',
    question: 'Why are the sensor recall and AUC 1.0?',
    answer:
      'The sensor test set is one held-out recording with 73 no-smoke and 35 smoke intervals, and the classes are cleanly separable there. That is a strong cross-recording result, not proof of perfect performance in every room. Balanced accuracy (98.6%) and smoke F1 (97.2%) are the safer summaries.',
  },
  {
    id: 'binary-vs-seven-class',
    category: 'metrics',
    question: 'Why is binary CSI 93.8% but 7-class CSI 86.2%?',
    answer:
      'They answer different questions. The binary view merges six non-smoke activities into one negative class, which is an easier task. Its 0.326 cut-off was chosen from the test score distribution, so it is descriptive and not a deployed, fully held-out gate.',
  },
  {
    id: 'unseen-session',
    category: 'limits',
    question: 'Why does the unseen session score only 30.6%?',
    answer:
      'That recording came from a new environment and was never used for training or tuning. The 7-class accuracy collapses, although smoke recall stays at 74% and the binary smoke AUC is 0.816. It is a domain-shift warning, not the headline in-domain result.',
  },
  {
    id: 'fusion-weights',
    category: 'fusion',
    question: 'Why 50/50 fusion weights?',
    answer:
      'Equal weights require real CSI evidence before a fusion alert and limit sensor-only false alarms. A sweep toward sensor-heavy weights raises simulated TPR (0.781 at 0.3/0.7) but also raises FPR and lets the sensor carry weak CSI cases; 50/50 is a deliberate safety choice.',
  },
  {
    id: 'platt-calibration',
    category: 'metrics',
    question: 'Why use Platt calibration if raw accuracy is higher?',
    answer:
      'The fused score needs comparable probabilities, and calibration improves probability quality (test Brier 0.213 → 0.199, log loss 0.451 → 0.443). It does not improve every metric: calibrated test accuracy drops slightly (86.2% → 85.7%), and ECE (0.045 → 0.064) is worse on test. Validation fitted the calibrator; test is diagnostics-only.',
  },
  {
    id: 'static-snapshot',
    category: 'limits',
    question: 'Are these numbers live?',
    answer:
      'No. They are a static snapshot extracted from the training notebooks and model sidecar JSONs. The live app uses the checkpoints in models/; update frontend/src/data/modelInfo.ts after retraining.',
  },
  {
    id: 'smoke-only',
    category: 'fusion',
    question: 'What is smoke_only?',
    answer:
      'A product extension: the sensor gate passes but CSI does not confirm the gesture (or the fused score is below 0.70). It is labelled “possible smoking activity” and is weaker than a confirmed fusion alert. The experimental csi_only class is off by default.',
  },
];

// ---------------------------------------------------------------------------
// Provenance
// ---------------------------------------------------------------------------

export interface ProvenanceItem {
  /** Compact visible label. */
  source: string;
  /** Repository-relative path; rendered as a GitHub link. */
  path: string;
  detail: string;
}

export const PROVENANCE: readonly ProvenanceItem[] = [
  {
    source: 'SmokeGuard_v2.ipynb',
    path: 'notebooks/SmokeGuard_v2.ipynb',
    detail:
      'CSI 7-class training and evaluation: split metadata, raw/calibrated test metrics and confusion matrices, binary smoke view, unseen-session check.',
  },
  {
    source: 'SmokeGuard_Smoke_Sensor.ipynb',
    path: 'notebooks/SmokeGuard_Smoke_Sensor.ipynb',
    detail:
      'Sensor SVM training, grouped out-of-fold threshold selection, cross-validation, and the file-holdout evaluation.',
  },
  {
    source: 'SmokeGuard_Fusion.ipynb',
    path: 'notebooks/SmokeGuard_Fusion.ipynb',
    detail:
      'Deployed fusion policy and the simulated cross-product evaluation (no paired CSI + sensor test set).',
  },
  {
    source: 'models/csi_resnet_lstm_7class.platt.json',
    path: 'smokeguard_application/models/csi_resnet_lstm_7class.platt.json',
    detail: 'CSI validation/test calibration diagnostics and class order (2026-10-30).',
  },
  {
    source: 'models/smoke_svm.meta.json',
    path: 'smokeguard_application/models/smoke_svm.meta.json',
    detail: 'Sensor holdout/OOF/CV metrics, threshold, and feature contract (2026-11-08).',
  },
  {
    source: 'app/config.py',
    path: 'smokeguard_application/app/config.py',
    detail: 'Deployed sensor gate, fused threshold, and fusion weights.',
  },
];
