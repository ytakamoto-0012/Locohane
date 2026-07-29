import type { IStep } from '@chainlit/react-client';

export function TokenUsageCard({ step }: { step: IStep | undefined }) {
  if (!step || typeof step.output !== 'string') return null;
  return (
    <div className="token-usage-card">
      <pre>{step.output}</pre>
    </div>
  );
}
