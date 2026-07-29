import { useState } from 'react';
import type { IStep } from '@chainlit/react-client';
import { StepList } from './StepList';
import { TokenUsageCard } from './TokenUsageCard';
import { WorkDirCard } from './WorkDirCard';
import { PlanCard } from './PlanCard';

export function SidePanel({
  sideSteps,
  tokenUsage,
  workDir,
  plan
}: {
  sideSteps: IStep[];
  tokenUsage: IStep | undefined;
  workDir: IStep | undefined;
  plan: IStep | undefined;
}) {
  const [collapsed, setCollapsed] = useState(false);

  if (collapsed) {
    return (
      <button type="button" className="side-panel-collapsed" onClick={() => setCollapsed(false)}>
        ◂ 詳細
      </button>
    );
  }

  return (
    <aside className="side-panel">
      <div className="side-panel-fixed">
        <WorkDirCard step={workDir} />
        <div className="side-panel-header">
          <span>実行の詳細</span>
          <button type="button" className="side-panel-toggle" onClick={() => setCollapsed(true)}>
            ▸
          </button>
        </div>
        <TokenUsageCard step={tokenUsage} />
        <PlanCard step={plan} />
      </div>
      <StepList steps={sideSteps} />
    </aside>
  );
}
