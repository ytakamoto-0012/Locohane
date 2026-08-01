import type { IStep } from '@chainlit/react-client';
import { TOKEN_USAGE_PREFIX } from '../utils/messageTree';

interface TokenUsageRow {
  label: string;
  input: number;
  output: number;
  total: number;
}

interface TokenUsagePayload {
  rows: TokenUsageRow[];
}

const fmt = (n: number) => n.toLocaleString('ja-JP');

export function TokenUsageCard({ step }: { step: IStep | undefined }) {
  if (!step || typeof step.output !== 'string') return null;

  let payload: TokenUsagePayload;
  try {
    payload = JSON.parse(step.output.slice(TOKEN_USAGE_PREFIX.length));
  } catch {
    return null;
  }
  if (!payload.rows?.length) return null;

  return (
    <div className="token-usage-card">
      <table className="token-usage-table">
        <thead>
          <tr>
            <th></th>
            <th>入力</th>
            <th>出力</th>
            <th>合計</th>
          </tr>
        </thead>
        <tbody>
          {payload.rows.map((row) => (
            <tr key={row.label}>
              <td>{row.label}</td>
              <td>{fmt(row.input)}</td>
              <td>{fmt(row.output)}</td>
              <td>{fmt(row.total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
