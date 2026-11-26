/**
 * Model Info — a read-only model FAQ / metrics reference.
 *
 * All numbers come from ../data/modelInfo.ts. This component only formats and
 * lays them out; it never fetches metrics at runtime.
 */

import { useState } from 'react';
import type { ReactNode, SyntheticEvent } from 'react';
import ConfusionMatrix from './ConfusionMatrix';
import { GITHUB_REPO, githubBlobUrl } from '../github';
import {
  CSI_BINARY_TEST,
  CSI_MODEL,
  CSI_TEST_CALIBRATED,
  CSI_TEST_RAW,
  CSI_UNSEEN,
  CSI_VALIDATION_ROWS,
  FAQ_CATEGORIES,
  FAQ_ITEMS,
  FUSION,
  METRIC_GROUPS,
  PROVENANCE,
  SENSOR_CV,
  SENSOR_MODEL,
  SENSOR_OOF,
  SENSOR_TEST,
  SNAPSHOT_DATE,
} from '../data/modelInfo';
import type {
  EvaluationView,
  MetricGroup,
  ModelFact,
} from '../data/modelInfo';

const pct = (value: number, digits = 1): string =>
  `${(value * 100).toFixed(digits)}%`;

const int = (value: number): string => value.toLocaleString();

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

function MetricTile({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="model-tile">
      <div className="model-tile-label">{label}</div>
      <div className="model-tile-value">{value}</div>
      {sub && <div className="model-tile-sub">{sub}</div>}
    </div>
  );
}

function Section({
  title,
  meta,
  children,
}: {
  title: string;
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="detail-section model-section">
      <div className="detail-section-header">
        <h3 className="model-section-title">{title}</h3>
        {meta}
      </div>
      {children}
    </section>
  );
}

function Callout({
  tone = 'info',
  title,
  children,
}: {
  tone?: 'info' | 'warn' | 'neutral';
  title: string;
  children: ReactNode;
}) {
  return (
    <div className={`model-callout model-callout-${tone}`}>
      <div className="model-callout-title">{title}</div>
      <div className="model-callout-body">{children}</div>
    </div>
  );
}

function Facts({ items }: { items: readonly ModelFact[] }) {
  return (
    <dl className="model-facts">
      {items.map((fact) => (
        <div className="model-fact" key={fact.label}>
          <dt>{fact.label}</dt>
          <dd>{fact.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function PerClassTable({ view }: { view: EvaluationView }) {
  return (
    <div className="model-table-scroll">
      <table className="model-table">
        <caption>
          Per-class results · {view.label} · n = {view.summary.n}
        </caption>
        <thead>
          <tr>
            <th scope="col">Class</th>
            <th scope="col" className="num">Precision</th>
            <th scope="col" className="num">Recall</th>
            <th scope="col" className="num">F1</th>
            <th scope="col" className="num">Support</th>
          </tr>
        </thead>
        <tbody>
          {view.perClass.map((row) => (
            <tr
              key={row.className}
              className={
                row.className === 'smoke' ? 'model-row-highlight' : undefined
              }
            >
              <th scope="row">{row.className}</th>
              <td className="num">{pct(row.precision)}</td>
              <td className="num">{pct(row.recall)}</td>
              <td className="num">{pct(row.f1)}</td>
              <td className="num">{row.support}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Glossary({ groups }: { groups: readonly MetricGroup[] }) {
  return (
    <div className="model-glossary">
      {groups.map((group) => (
        <div className="model-glossary-group" key={group.title}>
          <h4>{group.title}</h4>
          <dl>
            {group.items.map((item) => (
              <div className="model-glossary-item" key={item.term}>
                <dt>{item.term}</dt>
                <dd>{item.definition}</dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FAQ
// ---------------------------------------------------------------------------

function FaqSection() {
  const [openIds, setOpenIds] = useState<ReadonlySet<string>>(() => new Set());
  const allOpen = FAQ_ITEMS.every((item) => openIds.has(item.id));

  const setAll = (open: boolean) => {
    setOpenIds(open ? new Set(FAQ_ITEMS.map((item) => item.id)) : new Set());
  };

  const handleToggle =
    (id: string) => (event: SyntheticEvent<HTMLDetailsElement>) => {
      const isOpen = event.currentTarget.open;
      setOpenIds((prev) => {
        // Programmatic open changes also emit toggle events; ignore those
        // instead of recreating the set on every expand-all.
        if (prev.has(id) === isOpen) return prev;
        const next = new Set(prev);
        if (isOpen) next.add(id);
        else next.delete(id);
        return next;
      });
    };

  return (
    <Section title="Frequently asked questions">
      <p className="model-faq-intro">
        Short answers to the questions the metrics raise.
      </p>
      <div className="model-faq-toolbar">
        <span className="model-faq-hint">
          {FAQ_ITEMS.length} answers · select a question to expand
        </span>
        <button
          type="button"
          className="model-faq-toggle-all"
          aria-controls="model-faq-groups"
          aria-label={
            allOpen ? 'Collapse all FAQ answers' : 'Expand all FAQ answers'
          }
          onClick={() => setAll(!allOpen)}
        >
          {allOpen ? 'Collapse all' : 'Expand all'}
        </button>
      </div>
      <div id="model-faq-groups" className="model-faq-groups">
        {FAQ_CATEGORIES.map((group) => {
          const items = FAQ_ITEMS.filter(
            (item) => item.category === group.id,
          );
          return (
            <section
              key={group.id}
              className="model-faq-group"
              aria-labelledby={`faq-group-${group.id}`}
            >
              <h4
                id={`faq-group-${group.id}`}
                className="model-faq-group-title"
              >
                {group.title}
                <span className="model-faq-group-count" aria-hidden="true">
                  {items.length}
                </span>
              </h4>
              <div className="model-faq">
                {items.map((item, index) => (
                  <details
                    key={item.id}
                    className="model-faq-item"
                    open={openIds.has(item.id)}
                    onToggle={handleToggle(item.id)}
                  >
                    <summary>
                      <span className="model-faq-index" aria-hidden="true">
                        Q{index + 1}
                      </span>
                      <span className="model-faq-question">
                        {item.question}
                      </span>
                      <span className="model-faq-chevron" aria-hidden="true" />
                    </summary>
                    <div className="model-faq-answer">
                      <p>{item.answer}</p>
                    </div>
                  </details>
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ModelInfoView() {
  const calibrated = CSI_TEST_CALIBRATED;
  const raw = CSI_TEST_RAW;
  const smokeCalibrated = calibrated.perClass.find(
    (row) => row.className === 'smoke',
  );
  const sensor = SENSOR_TEST.metrics;
  const fusion = FUSION.simulation;

  return (
    <div className="model-view">
      <div className="model-page">
        <div className="history-toolbar">
          <div>
            <h2 className="model-title">Model Info</h2>
            <p className="model-lede">
              How SmokeGuard turns Wi-Fi CSI and air-quality readings into a
              smoking-activity alert — and how well each stage performs.
            </p>
          </div>
          <span className="badge">Snapshot · {SNAPSHOT_DATE}</span>
        </div>

        <Callout tone="info" title="Read each section in context">
          CSI, sensor, and fusion are evaluated on different held-out sets and
          units — windows, intervals, and simulated pairs. Their percentages
          answer different questions and are not directly comparable; the
          labels say which set each number comes from.
        </Callout>

        <Section
          title="How a detection is made"
          meta={<span className="detail-meta">evaluated every 0.2 s</span>}
        >
          <div className="model-flow">
            <div className="model-flow-step">
              <span className="model-flow-index">1</span>
              <div>
                <strong>Wi-Fi CSI</strong>
                <p>
                  A 4–7 s gesture window over 52 active subcarriers is
                  classified into one of seven activities; <code>smoke</code>{' '}
                  is the gesture of interest.
                </p>
              </div>
            </div>
            <div className="model-flow-step">
              <span className="model-flow-index">2</span>
              <div>
                <strong>Air quality</strong>
                <p>
                  A 14.9 s sensor window against a 600 s gas baseline produces
                  a smoke / no-smoke probability.
                </p>
              </div>
            </div>
            <div className="model-flow-step">
              <span className="model-flow-index">3</span>
              <div>
                <strong>Late fusion</strong>
                <p>
                  The two probabilities are averaged; gates decide whether the
                  result is a confirmed <code>fusion</code> alert or a weaker
                  possible alert.
                </p>
              </div>
            </div>
          </div>
          <div className="model-rule">
            <div className="model-rule-title">Deployed rule</div>
            <code>{FUSION.policy.formula}</code>
            <ul>
              <li>
                CSI gate: <code>{FUSION.policy.csiGate}</code>
              </li>
              <li>
                Sensor gate: <code>P_sensor ≥ {FUSION.policy.sensorGate.toFixed(4)}</code>
              </li>
              <li>
                Fused threshold: <code>score ≥ {FUSION.policy.fusedThreshold.toFixed(2)}</code>
              </li>
            </ul>
          </div>
        </Section>

        {/* ---------------- CSI model ---------------- */}
        <Section
          title="CSI model — 7-class gesture recognition"
          meta={<span className="badge">ResNet-LSTM</span>}
        >
          <p className="model-body">{CSI_MODEL.role}</p>
          <Facts items={CSI_MODEL.facts} />

          <div className="model-tiles">
            <MetricTile
              label="Test accuracy"
              value={pct(calibrated.summary.accuracy)}
              sub={`Calibrated · n = ${calibrated.summary.n}`}
            />
            <MetricTile
              label="Macro F1"
              value={pct(calibrated.summary.macroF1)}
              sub="Calibrated · 7 classes"
            />
            <MetricTile
              label="Smoke recall"
              value={smokeCalibrated ? pct(smokeCalibrated.recall) : '—'}
              sub="Of real smoking windows found"
            />
            <MetricTile
              label="Smoke F1"
              value={smokeCalibrated ? pct(smokeCalibrated.f1) : '—'}
              sub="Precision / recall balance"
            />
            <MetricTile
              label="Brier"
              value={calibrated.summary.brier?.toFixed(3) ?? '—'}
              sub="Probability error · lower is better"
            />
            <MetricTile
              label="ECE"
              value={calibrated.summary.ece?.toFixed(3) ?? '—'}
              sub="Calibration gap · lower is better"
            />
          </div>

          <div className="model-table-scroll">
            <table className="model-table">
              <caption>
                CSI accuracy and macro F1 by evaluation view
              </caption>
              <thead>
                <tr>
                  <th scope="col">View</th>
                  <th scope="col" className="num">Accuracy</th>
                  <th scope="col" className="num">Macro F1</th>
                </tr>
              </thead>
              <tbody>
                <tr className="model-row-highlight">
                  <th scope="row">Held-out test · calibrated (primary)</th>
                  <td className="num">{pct(calibrated.summary.accuracy)}</td>
                  <td className="num">{pct(calibrated.summary.macroF1)}</td>
                </tr>
                <tr>
                  <th scope="row">Held-out test · raw softmax</th>
                  <td className="num">{pct(raw.summary.accuracy)}</td>
                  <td className="num">{pct(raw.summary.macroF1)}</td>
                </tr>
                {CSI_VALIDATION_ROWS.map((row) => (
                  <tr key={row.key}>
                    <th scope="row">{row.label}</th>
                    <td className="num">{pct(row.accuracy)}</td>
                    <td className="num">{pct(row.macroF1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="model-note">
            Validation fitted the Platt calibrator; the test split is
            diagnostics-only. The deployed CSI gate uses the calibrated argmax,
            while the fused score uses a separate binary Platt smoke
            probability.
          </p>

          <PerClassTable view={calibrated} />
          <ConfusionMatrix
            data={calibrated.confusion}
            caption="CSI test confusion matrix — calibrated argmax"
            note={`n = ${calibrated.summary.n} · rows = actual · columns = predicted · bold diagonal = correct`}
          />

          <details className="model-details">
            <summary>Raw softmax results (the notebook headline)</summary>
            <div className="model-details-body">
              <p>
                Raw softmax is slightly more accurate than the calibrated
                argmax, but calibration improves probability quality. The
                deployed fusion stage needs comparable probabilities, so the
                primary view above is calibrated.
              </p>
              <div className="model-table-scroll">
                <table className="model-table">
                  <caption>CSI probability quality · held-out test</caption>
                  <thead>
                    <tr>
                      <th scope="col">Score</th>
                      <th scope="col" className="num">Brier</th>
                      <th scope="col" className="num">ECE</th>
                      <th scope="col" className="num">Log loss</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <th scope="row">Raw softmax</th>
                      <td className="num">{raw.summary.brier?.toFixed(3)}</td>
                      <td className="num">{raw.summary.ece?.toFixed(3)}</td>
                      <td className="num">{raw.summary.logLoss?.toFixed(3)}</td>
                    </tr>
                    <tr>
                      <th scope="row">Platt-calibrated</th>
                      <td className="num">{calibrated.summary.brier?.toFixed(3)}</td>
                      <td className="num">{calibrated.summary.ece?.toFixed(3)}</td>
                      <td className="num">{calibrated.summary.logLoss?.toFixed(3)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <PerClassTable view={raw} />
              <ConfusionMatrix
                data={raw.confusion}
                caption="CSI test confusion matrix — raw softmax argmax"
                note={`n = ${raw.summary.n} · rows = actual · columns = predicted · bold diagonal = correct`}
              />
            </div>
          </details>

          <details className="model-details">
            <summary>Binary smoke view (descriptive, not the deployed gate)</summary>
            <div className="model-details-body">
              <p>
                This view collapses the six non-smoke classes into one negative
                class and scores raw P(smoke) at a session-quantile threshold of{' '}
                <code>{CSI_BINARY_TEST.threshold.toFixed(4)}</code>. That cutoff
                was chosen from the test score distribution, so it is a
                descriptive notebook view — not the deployed argmax gate and not
                a fully held-out operating point.
              </p>
              <dl className="model-facts">
                <div className="model-fact">
                  <dt>Accuracy</dt>
                  <dd>{pct(CSI_BINARY_TEST.metrics.accuracy)}</dd>
                </div>
                <div className="model-fact">
                  <dt>Balanced accuracy</dt>
                  <dd>{pct(CSI_BINARY_TEST.metrics.balancedAccuracy)}</dd>
                </div>
                <div className="model-fact">
                  <dt>Smoke precision / recall / F1</dt>
                  <dd>
                    {pct(CSI_BINARY_TEST.metrics.precision)} /{' '}
                    {pct(CSI_BINARY_TEST.metrics.recall)} /{' '}
                    {pct(CSI_BINARY_TEST.metrics.f1)}
                  </dd>
                </div>
                <div className="model-fact">
                  <dt>ROC-AUC / AP</dt>
                  <dd>
                    {CSI_BINARY_TEST.metrics.rocAuc?.toFixed(3)} /{' '}
                    {CSI_BINARY_TEST.metrics.averagePrecision?.toFixed(3)}
                  </dd>
                </div>
              </dl>
              <ConfusionMatrix
                data={CSI_BINARY_TEST.confusion}
                caption="CSI binary smoke confusion matrix — descriptive threshold"
                note={`threshold = ${CSI_BINARY_TEST.threshold.toFixed(4)} · n = ${CSI_BINARY_TEST.metrics.n}`}
                size="compact"
                showColTotals
                showRowPercent
              />
            </div>
          </details>

          <details className="model-details">
            <summary>Domain-shift check — unseen session 2026-10-31</summary>
            <div className="model-details-body">
              <p>
                This recording came from a new environment and was never used
                for training or tuning. The seven-class result collapses to
                about 30%; seven-class smoke recall stays high because the
                model over-predicts smoke there, but the binary smoke view is
                weak (AUC 0.728, AP 0.240). The archived smoke-6.0 checkpoint
                scored higher on this session (AP 0.441), but the smoke-1.0
                checkpoint is the active model by the chosen configuration.
                Treat this as a domain-shift warning, not the headline result.
              </p>
              <dl className="model-facts">
                <div className="model-fact">
                  <dt>7-class accuracy</dt>
                  <dd>{pct(CSI_UNSEEN.metrics.accuracy)}</dd>
                </div>
                <div className="model-fact">
                  <dt>7-class macro F1</dt>
                  <dd>{pct(CSI_UNSEEN.metrics.macroF1)}</dd>
                </div>
                <div className="model-fact">
                  <dt>Smoke precision / recall / F1</dt>
                  <dd>
                    {pct(CSI_UNSEEN.smoke.precision)} /{' '}
                    {pct(CSI_UNSEEN.smoke.recall)} /{' '}
                    {pct(CSI_UNSEEN.smoke.f1)}
                  </dd>
                </div>
                <div className="model-fact">
                  <dt>Binary smoke accuracy / balanced</dt>
                  <dd>
                    {pct(CSI_UNSEEN.binary.metrics.accuracy)} /{' '}
                    {pct(CSI_UNSEEN.binary.metrics.balancedAccuracy)}
                  </dd>
                </div>
                <div className="model-fact">
                  <dt>Binary smoke P / R / F1</dt>
                  <dd>
                    {pct(CSI_UNSEEN.binary.metrics.precision)} /{' '}
                    {pct(CSI_UNSEEN.binary.metrics.recall)} /{' '}
                    {pct(CSI_UNSEEN.binary.metrics.f1)}
                  </dd>
                </div>
                <div className="model-fact">
                  <dt>Binary smoke AUC / AP</dt>
                  <dd>
                    {CSI_UNSEEN.binary.metrics.rocAuc?.toFixed(3)} /{' '}
                    {CSI_UNSEEN.binary.metrics.averagePrecision?.toFixed(3)}
                  </dd>
                </div>
              </dl>
              <ConfusionMatrix
                data={CSI_UNSEEN.binary.confusion}
                caption="Unseen-session binary smoke confusion matrix"
                note={`n = ${CSI_UNSEEN.binary.metrics.n} · 50 windows per class`}
                size="compact"
                showColTotals
                showRowPercent
              />
            </div>
          </details>
        </Section>

        {/* ---------------- Sensor model ---------------- */}
        <Section
          title="Sensor model — air-quality smoke detection"
          meta={<span className="badge">RBF SVM</span>}
        >
          <p className="model-body">{SENSOR_MODEL.role}</p>
          <Facts items={SENSOR_MODEL.facts} />

          <div className="model-tiles">
            <MetricTile
              label="Holdout accuracy"
              value={pct(sensor.accuracy)}
              sub="n = 108 intervals"
            />
            <MetricTile
              label="Balanced accuracy"
              value={pct(sensor.balancedAccuracy)}
              sub="Classes weighted equally"
            />
            <MetricTile
              label="Smoke recall"
              value={pct(sensor.recall)}
              sub="Of real smoking intervals found"
            />
            <MetricTile
              label="Smoke precision"
              value={pct(sensor.precision)}
              sub="Of smoke alerts that were real"
            />
            <MetricTile
              label="Smoke F1"
              value={pct(sensor.f1)}
              sub="Precision / recall balance"
            />
            <MetricTile
              label="ROC-AUC"
              value={sensor.rocAuc?.toFixed(2) ?? '—'}
              sub="Ranking quality · 0.5 chance"
            />
          </div>

          <ConfusionMatrix
            data={SENSOR_TEST.confusion}
            caption="Sensor holdout confusion matrix"
            note="n = 108 intervals · rows = actual · columns = predicted · bold diagonal = correct"
            size="compact"
            showColTotals
            showRowPercent
          />

          <Callout tone="neutral" title="One recording, mostly negatives">
            The test file has 73 no-smoke and 35 smoke intervals from one
            recording; the classes are cleanly separable there. ROC-AUC and AP
            of 1.0 are a property of this holdout, not proof of perfection in
            every room. Balanced accuracy (98.6%) and smoke F1 (97.2%) are the
            safer summaries.
          </Callout>

          <div className="model-subheading">Training robustness</div>
          <dl className="model-facts">
            <div className="model-fact">
              <dt>Out-of-fold F1</dt>
              <dd>{pct(SENSOR_OOF.f1)}</dd>
            </div>
            <div className="model-fact">
              <dt>OOF precision / recall</dt>
              <dd>
                {pct(SENSOR_OOF.precision)} / {pct(SENSOR_OOF.recall)}
              </dd>
            </div>
            <div className="model-fact">
              <dt>5-fold grouped CV F1</dt>
              <dd>
                {pct(SENSOR_CV.meanF1)} ± {pct(SENSOR_CV.stdF1)}
              </dd>
            </div>
            <div className="model-fact">
              <dt>Gate threshold</dt>
              <dd>
                {FUSION.policy.sensorGate.toFixed(4)} (F1-max on grouped OOF)
              </dd>
            </div>
          </dl>
          <p className="model-note">
            The threshold is tuned on grouped out-of-fold training
            predictions; the test file was never used for fitting, CV, or
            threshold selection.
          </p>
        </Section>

        {/* ---------------- Late fusion ---------------- */}
        <Section
          title="Late fusion — combining CSI and sensor"
          meta={<span className="badge">Simulated evaluation</span>}
        >
          <Callout tone="warn" title="Simulation, not paired data">
            The CSI and sensor test sets were recorded in different sessions.
            Every CSI test window ({fusion.csiWindows}) is combined with every
            sensor test interval ({fusion.sensorIntervals}) as{' '}
            {int(fusion.pairs)} artificial pairs: {int(fusion.positives)}{' '}
            positive and {int(fusion.negatives)} negative. These are not
            synchronized recordings or real co-occurrences. TPR, FPR, Youden J,
            and the class-conditional rates are the interpretable outputs;
            precision, F1, and accuracy depend on the artificial{' '}
            {pct(fusion.prevalence)} prevalence and are not deployment
            estimates.
          </Callout>

          <Callout tone="info" title="What the app alerts on is not what this matrix counts">
            The product alerts on <code>fusion</code> and{' '}
            <code>smoke_only</code>. The tables here count confirmed{' '}
            <code>fusion</code> alerts only, matching the paper.{' '}
            <code>smoke_only</code> fires on the sensor gate alone, so the
            product’s total alert sensitivity is higher (with more false
            alarms) than the fusion-only TPR below. <code>csi_only</code> is
            experimental and off by default.
          </Callout>

          <div className="model-rule">
            <div className="model-rule-title">Deployed fusion policy</div>
            <code>{FUSION.policy.formula}</code>
            <ul>
              {FUSION.policy.labels.map((item) => (
                <li key={item.label}>
                  <code>{item.label}</code> — {item.text}
                </li>
              ))}
            </ul>
          </div>

          <div className="model-tiles">
            <MetricTile
              label="Simulated TPR"
              value={pct(fusion.tpr)}
              sub="Of simulated smoking pairs alerted"
            />
            <MetricTile
              label="Simulated FPR"
              value={pct(fusion.fpr, 2)}
              sub="Of simulated no-smoking pairs alerted"
            />
            <MetricTile
              label="Youden J"
              value={fusion.youdenJ.toFixed(3)}
              sub="TPR − FPR · 0 chance, 1 perfect"
            />
            <MetricTile
              label="Simulated pairs"
              value={int(fusion.pairs)}
              sub={`${int(fusion.positives)} positive · ${int(fusion.negatives)} negative`}
            />
          </div>

          <ConfusionMatrix
            data={fusion.confusion}
            caption="Simulated fusion outcomes"
            note={`${int(fusion.pairs)} artificial pairs · rows = actual state · columns = alert decision`}
            size="compact"
            showColTotals
            showRowPercent
          />

          <div className="model-subheading">
            Fusion alerts by CSI class (while the sensor gate passes)
          </div>
          <div className="model-table-scroll">
            <table className="model-table">
              <caption>
                Class-conditional fusion alert rate · pairs = CSI windows × 35
                smoke-state sensor intervals
              </caption>
              <thead>
                <tr>
                  <th scope="col">CSI class</th>
                  <th scope="col" className="num">Fusion alerts</th>
                  <th scope="col" className="num">Pairs</th>
                  <th scope="col">Alert rate</th>
                </tr>
              </thead>
              <tbody>
                {fusion.byClass.map((row) => {
                  const rate = row.pairs === 0 ? 0 : row.alerts / row.pairs;
                  return (
                    <tr
                      key={row.className}
                      className={
                        row.className === 'smoke'
                          ? 'model-row-highlight'
                          : undefined
                      }
                    >
                      <th scope="row">{row.className}</th>
                      <td className="num">{int(row.alerts)}</td>
                      <td className="num">{int(row.pairs)}</td>
                      <td>
                        <div className="model-rate">
                          <div className="model-rate-track">
                            <div
                              className="model-rate-fill"
                              style={{ width: `${rate * 100}%` }}
                            />
                          </div>
                          <span className="model-rate-value">{pct(rate)}</span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="model-note">
            When the sensor says no-smoke, the fusion alert count is 0 for
            every CSI class. The 50/50 weights are a deliberate safety choice:
            a sensor-heavy sweep raises simulated TPR but also raises FPR and
            requires less CSI evidence.
          </p>

          <details className="model-details">
            <summary>All simulation metrics (prevalence-dependent)</summary>
            <div className="model-details-body">
              <dl className="model-facts">
                <div className="model-fact">
                  <dt>Precision</dt>
                  <dd>{pct(fusion.precision)}</dd>
                </div>
                <div className="model-fact">
                  <dt>F1</dt>
                  <dd>{pct(fusion.f1)}</dd>
                </div>
                <div className="model-fact">
                  <dt>Balanced accuracy</dt>
                  <dd>{pct(fusion.balancedAccuracy)}</dd>
                </div>
                <div className="model-fact">
                  <dt>Simulated accuracy</dt>
                  <dd>{pct(fusion.simulatedAccuracy)}</dd>
                </div>
                <div className="model-fact">
                  <dt>Simulated prevalence</dt>
                  <dd>{pct(fusion.prevalence)}</dd>
                </div>
                <div className="model-fact">
                  <dt>Counts (TP / FP / FN / TN)</dt>
                  <dd>
                    {int(fusion.tp)} / {int(fusion.fp)} / {int(fusion.fn)} /{' '}
                    {int(fusion.tn)}
                  </dd>
                </div>
              </dl>
              <p className="model-note">
                Simulated accuracy looks high because {pct(fusion.prevalence)}{' '}
                of the artificial pairs are positive; it is not a deployment
                estimate. Precision and F1 move with that prevalence too.
              </p>
            </div>
          </details>
        </Section>

        {/* ---------------- Metric guide ---------------- */}
        <Section
          title="Metrics, in plain language"
          meta={<span className="detail-meta">how to read the numbers above</span>}
        >
          <div className="model-example">
            <strong>Worked example — the sensor matrix.</strong> The row
            “smoke” and column “no-smoke” cell is 0: no real smoking interval
            was missed. The row “no-smoke” and column “smoke” cell is 2: two
            clean intervals were flagged. That gives smoke precision{' '}
            {pct(sensor.precision)} (35 of 37 alerts were real), smoke recall{' '}
            {pct(sensor.recall)} (35 of 35 real smoking intervals were found),
            and smoke F1 {pct(sensor.f1)}. The 71 truly clean intervals in the
            top-left cell never show up in precision or recall, which is why
            the full matrix is shown.
          </div>
          <Glossary groups={METRIC_GROUPS} />
        </Section>

        {/* ---------------- FAQ ---------------- */}
        <FaqSection />

        {/* ---------------- Provenance ---------------- */}
        <Section title="Provenance and scope">
          <dl className="model-provenance">
            {PROVENANCE.map((item) => (
              <div key={item.path} className="model-provenance-item">
                <dt>
                  <a
                    className="model-provenance-link"
                    href={githubBlobUrl(item.path)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {item.source}
                    <span className="model-ext" aria-hidden="true">
                      {' '}
                      ↗
                    </span>
                    <span className="visually-hidden">
                      {' '}
                      (opens in a new tab)
                    </span>
                  </a>
                </dt>
                <dd>{item.detail}</dd>
              </div>
            ))}
          </dl>
          <p className="model-provenance-footer">
            <a
              className="model-provenance-link"
              href={GITHUB_REPO}
              target="_blank"
              rel="noopener noreferrer"
            >
              View repository on GitHub
              <span className="model-ext" aria-hidden="true">
                {' '}
                ↗
              </span>
              <span className="visually-hidden"> (opens in a new tab)</span>
            </a>
            <span>
              Source links open the <code>main</code> branch in a new tab.
            </span>
          </p>
          <p className="model-note">
            Static snapshot {SNAPSHOT_DATE}. These values are not fetched at
            runtime. After retraining, re-extract them from the notebooks and
            model sidecars and update{' '}
            <code>frontend/src/data/modelInfo.ts</code>; the deployed
            thresholds live in <code>app/config.py</code>.
          </p>
        </Section>
      </div>
    </div>
  );
}
