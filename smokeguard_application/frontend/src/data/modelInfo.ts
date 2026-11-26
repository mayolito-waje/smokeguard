/**
 * Model Info page data — a static snapshot of the training/evaluation results.
 *
 * Sources (extracted 2026-11-26), at these paths in the GitHub repository:
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

export const SNAPSHOT_DATE = '2026-11-26';

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
    { label: 'Train / val / test', value: '2,067 / 279 / 279' },
    { label: 'Best val macro-F1', value: '0.8293 @ epoch 135' },
    { label: 'Trained', value: '2026-11-26' },
  ] satisfies readonly ModelFact[],
} as const;

const CSI_RAW_PER_CLASS: readonly PerClassMetric[] = [
  { className: 'smoke', precision: 0.7419355, recall: 0.8070175, f1: 0.7731092, support: 57 },
  { className: 'eat', precision: 0.7560976, recall: 0.8157895, f1: 0.7848101, support: 38 },
  { className: 'drink', precision: 0.6666667, recall: 0.6315789, f1: 0.6486486, support: 38 },
  { className: 'sit', precision: 0.8611111, recall: 0.8857143, f1: 0.8732394, support: 35 },
  { className: 'stand', precision: 0.9166667, recall: 0.9428571, f1: 0.9295775, support: 35 },
  { className: 'random', precision: 0.78125, recall: 0.6578947, f1: 0.7142857, support: 38 },
  { className: 'static', precision: 0.9444444, recall: 0.8947368, f1: 0.9189189, support: 38 },
];

const CSI_CALIBRATED_PER_CLASS: readonly PerClassMetric[] = [
  { className: 'smoke', precision: 0.7301587, recall: 0.8070175, f1: 0.7666667, support: 57 },
  { className: 'eat', precision: 0.7560976, recall: 0.8157895, f1: 0.7848101, support: 38 },
  { className: 'drink', precision: 0.6571429, recall: 0.6052632, f1: 0.630137, support: 38 },
  { className: 'sit', precision: 0.8611111, recall: 0.8857143, f1: 0.8732394, support: 35 },
  { className: 'stand', precision: 0.9166667, recall: 0.9428571, f1: 0.9295775, support: 35 },
  { className: 'random', precision: 0.8, recall: 0.6315789, f1: 0.7058824, support: 38 },
  { className: 'static', precision: 0.9210526, recall: 0.9210526, f1: 0.9210526, support: 38 },
];

// Rows = actual, columns = predicted, in CSI_CLASSES order.
const CSI_RAW_CONFUSION: ConfusionData = {
  rowLabels: CSI_CLASSES,
  colLabels: CSI_CLASSES,
  counts: [
    [46, 3, 5, 1, 0, 1, 1],
    [2, 31, 5, 0, 0, 0, 0],
    [5, 6, 24, 1, 0, 2, 0],
    [1, 0, 0, 31, 2, 1, 0],
    [0, 0, 0, 2, 33, 0, 0],
    [7, 1, 2, 1, 1, 25, 1],
    [1, 0, 0, 0, 0, 3, 34],
  ],
};

const CSI_CALIBRATED_CONFUSION: ConfusionData = {
  rowLabels: CSI_CLASSES,
  colLabels: CSI_CLASSES,
  counts: [
    [46, 3, 5, 1, 0, 1, 1],
    [2, 31, 5, 0, 0, 0, 0],
    [6, 6, 23, 1, 0, 2, 0],
    [1, 0, 0, 31, 2, 1, 0],
    [0, 0, 0, 2, 33, 0, 0],
    [7, 1, 2, 1, 1, 24, 2],
    [1, 0, 0, 0, 0, 2, 35],
  ],
};

export const CSI_TEST_CALIBRATED: EvaluationView = {
  key: 'test-calibrated',
  label: 'Platt-calibrated argmax · held-out test',
  split: 'Held-out test · 279 windows · 57 / 38 / 38 / 35 / 35 / 38 / 38',
  summary: {
    n: 279,
    accuracy: 0.7992831541218638,
    balancedAccuracy: 0.8013247404224847,
    macroF1: 0.8016236664969841,
    brier: 0.2867517364095143,
    ece: 0.09769516781639975,
    logLoss: 0.6083009745883816,
  },
  perClass: CSI_CALIBRATED_PER_CLASS,
  confusion: CSI_CALIBRATED_CONFUSION,
};

export const CSI_TEST_RAW: EvaluationView = {
  key: 'test-raw',
  label: 'Raw softmax argmax · held-out test (notebook headline)',
  split: 'Held-out test · 279 windows · 57 / 38 / 38 / 35 / 35 / 38 / 38',
  summary: {
    n: 279,
    accuracy: 0.8028673835125448,
    balancedAccuracy: 0.8050841389187253,
    macroF1: 0.8060842219344986,
    brier: 0.2959814199532609,
    ece: 0.09023616850162486,
    logLoss: 0.6172807271131416,
  },
  perClass: CSI_RAW_PER_CLASS,
  confusion: CSI_RAW_CONFUSION,
};

/** Validation aggregate rows only — no validation per-class table/matrix exists. */
export const CSI_VALIDATION_ROWS: readonly ValidationRow[] = [
  { key: 'val-raw', label: 'Validation · raw softmax', n: 279, accuracy: 0.8207885304659498, macroF1: 0.8292699133431028 },
  { key: 'val-cal', label: 'Validation · Platt-calibrated', n: 279, accuracy: 0.8279569892473119, macroF1: 0.8358238165512848 },
];

/** Binary smoke diagnostic at the notebook's session-quantile threshold. */
export const CSI_BINARY_TEST = {
  label: 'Binary smoke view · held-out test',
  threshold: 0.602046012878418,
  metrics: {
    n: 279,
    accuracy: 0.9139784946236559,
    balancedAccuracy: 0.8677098150782361,
    precision: 0.7894736842,
    recall: 0.7894736842,
    f1: 0.7894736842,
    rocAuc: 0.9480796586059744,
    averagePrecision: 0.8500116025870696,
  } satisfies BinaryMetrics,
  confusion: {
    rowLabels: ['no-smoke', 'smoke'],
    colLabels: ['no-smoke', 'smoke'],
    counts: [
      [210, 12],
      [12, 45],
    ],
  } satisfies ConfusionData,
};

/** Unseen-session domain-shift probe — caveat, not a headline result. */
export const CSI_UNSEEN = {
  label: 'Unseen session 2026-10-31',
  metrics: {
    n: 350,
    accuracy: 0.3029,
    balancedAccuracy: 0.3029,
    macroF1: 0.1917,
  } satisfies SummaryMetrics,
  smoke: { precision: 0.2335, recall: 0.92, f1: 0.3725 },
  binary: {
    metrics: {
      n: 350,
      accuracy: 0.7542857142857143,
      balancedAccuracy: 0.59,
      precision: 0.25,
      recall: 0.36,
      f1: 0.2951,
      rocAuc: 0.7279,
      averagePrecision: 0.2403,
    } satisfies BinaryMetrics,
    confusion: {
      rowLabels: ['no-smoke', 'smoke'],
      colLabels: ['no-smoke', 'smoke'],
      counts: [
        [246, 54],
        [32, 18],
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
    csiWindows: 279,
    sensorIntervals: 108,
    pairs: 30132,
    positives: 1995,
    negatives: 28137,
    prevalence: 0.06620868180007965,
    tp: 1564,
    fp: 515,
    fn: 431,
    tn: 27622,
    tpr: 0.7839598997493734,
    fpr: 0.01830330170238476,
    youdenJ: 0.7656565980469886,
    precision: 0.7522847522847523,
    recall: 0.7839598997493734,
    f1: 0.7677957781050565,
    balancedAccuracy: 0.8828282990234944,
    simulatedAccuracy: 0.9686048055,
    confusion: {
      rowLabels: ['smoking', 'no-smoking'],
      colLabels: ['alert', 'no alert'],
      counts: [
        [1564, 431],
        [515, 27622],
      ],
    } satisfies ConfusionData,
    byClass: [
      { className: 'smoke', alerts: 1564, pairs: 1995 },
      { className: 'eat', alerts: 63, pairs: 1330 },
      { className: 'drink', alerts: 182, pairs: 1330 },
      { className: 'sit', alerts: 0, pairs: 1225 },
      { className: 'stand', alerts: 0, pairs: 1225 },
      { className: 'random', alerts: 235, pairs: 1330 },
      { className: 'static', alerts: 35, pairs: 1330 },
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
          'How many true examples of a class were in the test set. The CSI test set has 279 windows: 57 smoke and 35–38 for each other class.',
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
      'The three parts are evaluated on different data and units: CSI on 279 test windows, the sensor on 108 held-out intervals, and fusion on 30,132 artificial CSI × sensor pairs. Their percentages answer different questions and are not directly comparable.',
  },
  {
    id: 'fusion-tpr-provenance',
    category: 'fusion',
    question: 'Is the 78% fusion TPR real paired data?',
    answer:
      'No. No synchronized CSI + sensor test recording exists. The fusion evaluation pairs every CSI test window with every sensor test interval (279 × 108 = 30,132 artificial pairs). TPR, FPR, Youden J, and the per-class rates are the interpretable results. Precision, F1, and accuracy depend on the artificial 6.6% positive prevalence.',
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
    question: 'Why is binary CSI 91.4% but 7-class CSI 80.3%?',
    answer:
      'They answer different questions. The binary view merges six non-smoke activities into one negative class, which is an easier task. Its 0.602 cut-off was chosen from the test score distribution, so it is descriptive and not a deployed, fully held-out gate.',
  },
  {
    id: 'unseen-session',
    category: 'limits',
    question: 'Why does the unseen session score only 30.3%?',
    answer:
      'That recording came from a new environment and was never used for training or tuning. The 7-class accuracy collapses to 30.3%, and the binary smoke AUC falls to 0.728 (AP 0.240). Seven-class smoke recall stays high at 92% because the model over-predicts smoke there, but only 36% of smoke windows survive the deployed binary threshold. The archived smoke-6.0 checkpoint scored AP 0.441 on this session; the smoke-1.0 checkpoint is the active model by the chosen training configuration. Treat this as a domain-shift warning, not the headline in-domain result.',
  },
  {
    id: 'fusion-weights',
    category: 'fusion',
    question: 'Why 50/50 fusion weights?',
    answer:
      'Equal weights require real CSI evidence before a fusion alert and limit sensor-only false alarms. A sweep toward sensor-heavy weights raises simulated TPR (0.807 at 0.3/0.7) but also raises FPR (0.021 vs 0.018) and lets the sensor carry weak CSI cases; 50/50 is a deliberate safety choice.',
  },
  {
    id: 'platt-calibration',
    category: 'metrics',
    question: 'Why use Platt calibration if raw accuracy is higher?',
    answer:
      'The fused score needs comparable probabilities, and calibration improves probability quality (test Brier 0.296 → 0.287, log loss 0.617 → 0.608). It does not improve every metric: calibrated test accuracy drops slightly (80.3% → 79.9%), and ECE (0.090 → 0.098) is worse on test. Validation fitted the calibrator; test is diagnostics-only.',
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
    source: 'models/csi_resnet_lstm_7class.meta.json',
    path: 'smokeguard_application/models/csi_resnet_lstm_7class.meta.json',
    detail: 'Active training run: smoke-1.0, best epoch 135, val macro-F1 0.8293, frozen split c348ee3c (2026-11-26).',
  },
  {
    source: 'models/csi_resnet_lstm_7class.platt.json',
    path: 'smokeguard_application/models/csi_resnet_lstm_7class.platt.json',
    detail: 'CSI validation/test calibration diagnostics and class order (2026-11-26; n_fit = 279).',
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
