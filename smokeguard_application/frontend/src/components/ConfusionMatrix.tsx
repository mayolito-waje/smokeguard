/**
 * Accessible confusion-matrix table.
 *
 * Rows are the actual class/state; columns are the prediction/decision. The
 * numbers are always visible, so colour is an enhancement rather than the
 * only channel. Shading is the share of the actual row (not the global
 * count): this keeps the minority rows visible in matrices such as the
 * simulated fusion one, where negatives outnumber positives ~20:1.
 */

import type { ConfusionData } from '../data/modelInfo';

interface ConfusionMatrixProps {
  data: ConfusionData;
  caption: string;
  note?: string;
  size?: 'regular' | 'compact';
  showRowTotals?: boolean;
  showColTotals?: boolean;
  showRowPercent?: boolean;
}

type ShadeClass = 'cm-zero' | 'cm-shade-1' | 'cm-shade-2' | 'cm-shade-3' | 'cm-shade-4';

function shadeClass(count: number, rowTotal: number): ShadeClass {
  if (count === 0 || rowTotal === 0) return 'cm-zero';
  const share = count / rowTotal;
  if (share < 0.1) return 'cm-shade-1';
  if (share < 0.25) return 'cm-shade-2';
  if (share < 0.5) return 'cm-shade-3';
  return 'cm-shade-4';
}

function percent(part: number, whole: number): string {
  return whole === 0 ? '0.0%' : `${((part / whole) * 100).toFixed(1)}%`;
}

export default function ConfusionMatrix({
  data,
  caption,
  note,
  size = 'regular',
  showRowTotals = true,
  showColTotals = false,
  showRowPercent = false,
}: ConfusionMatrixProps) {
  const rowTotals = data.counts.map((row) =>
    row.reduce((sum, value) => sum + value, 0),
  );
  const colTotals = data.colLabels.map((_, colIndex) =>
    data.counts.reduce((sum, row) => sum + row[colIndex], 0),
  );
  const grandTotal = rowTotals.reduce((sum, value) => sum + value, 0);
  // Our square matrices (including the fusion decision matrix) put the
  // correct outcomes on the diagonal; non-square matrices have no diagonal.
  const isSquare = data.rowLabels.length === data.colLabels.length;

  return (
    <div
      className={`cm-scroll ${size === 'compact' ? 'cm-compact' : ''}`}
      role="region"
      aria-label={caption}
      tabIndex={0}
    >
      <table className="cm-table">
        <caption>
          <span className="cm-caption">{caption}</span>
          {note && <span className="cm-note">{note}</span>}
        </caption>
        <thead>
          <tr>
            <td className="cm-corner" aria-hidden="true">
              <span>actual ↓</span>
              <span>predicted →</span>
            </td>
            {data.colLabels.map((label, index) => (
              <th key={`${label}-${index}`} scope="col" className="cm-axis">
                {label}
              </th>
            ))}
            {showRowTotals && (
              <th scope="col" className="cm-total">
                Total
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {data.counts.map((row, rowIndex) => {
            const rowTotal = rowTotals[rowIndex];
            return (
              <tr key={data.rowLabels[rowIndex]}>
                <th scope="row" className="cm-axis">
                  {data.rowLabels[rowIndex]}
                </th>
                {row.map((count, colIndex) => {
                  const isDiagonal = isSquare && rowIndex === colIndex;
                  const classes = [
                    'cm-cell',
                    shadeClass(count, rowTotal),
                    isDiagonal ? 'cm-diagonal' : '',
                  ]
                    .filter(Boolean)
                    .join(' ');
                  const rowPercent = percent(count, rowTotal);
                  return (
                    <td
                      key={`${rowIndex}-${colIndex}`}
                      className={classes}
                      title={
                        `Actual ${data.rowLabels[rowIndex]}, predicted `
                        + `${data.colLabels[colIndex]}: ${count.toLocaleString()} `
                        + `(${rowPercent} of the actual row)`
                      }
                    >
                      <span className="cm-count">
                        {count.toLocaleString()}
                      </span>
                      {showRowPercent && count > 0 && (
                        <span className="cm-pct">{rowPercent}</span>
                      )}
                    </td>
                  );
                })}
                {showRowTotals && (
                  <td className="cm-total">{rowTotal.toLocaleString()}</td>
                )}
              </tr>
            );
          })}
        </tbody>
        {showColTotals && (
          <tfoot>
            <tr>
              <th scope="row" className="cm-total">
                Total
              </th>
              {colTotals.map((total, index) => (
                <td key={index} className="cm-total">
                  {total.toLocaleString()}
                </td>
              ))}
              {showRowTotals && (
                <td className="cm-total cm-grand">
                  {grandTotal.toLocaleString()}
                </td>
              )}
            </tr>
          </tfoot>
        )}
      </table>

      <div className="cm-legend">
        <span className="cm-legend-label">Share of actual row</span>
        <span className="cm-swatch cm-shade-1" aria-hidden="true" />
        <span className="cm-swatch cm-shade-2" aria-hidden="true" />
        <span className="cm-swatch cm-shade-3" aria-hidden="true" />
        <span className="cm-swatch cm-shade-4" aria-hidden="true" />
        <span className="cm-legend-label">light → dark</span>
      </div>
      <p className="cm-help">
        Rows are what really happened; columns are what the model predicted.
        {isSquare ? ' The bold diagonal is correct.' : ''}
      </p>
    </div>
  );
}
