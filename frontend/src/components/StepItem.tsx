import { useState } from 'react';
import type { IStep } from '@chainlit/react-client';

const TYPE_LABELS: Record<string, string> = {
  tool: 'ツール',
  llm: '思考中',
  embedding: 'embedding',
  retrieval: '検索',
  rerank: 'rerank',
  system_message: 'システム'
};

function formatContent(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

const STATUS_LABELS = {
  running: '実行中',
  done: '完了',
  error: 'エラー',
  stopped: '停止'
} as const;

export function StepItem({ step }: { step: IStep }) {
  const [open, setOpen] = useState(false);
  const hasContent = Boolean(step.input || step.output);
  // dispatch_agent（サブエージェント実行）配下のツール呼び出しは、実際には
  // 別のRunnableとして委譲元Stepの下に正しくネストされる（app.pyの
  // _resolve_parent_id 参照）。ここで子Stepを折りたたみ内に再帰表示することで、
  // 「サブエージェント実行中の間、その内部で何が起きているか」が見える。
  const children = step.steps ?? [];
  const hasChildren = children.length > 0;
  const expandable = hasContent || hasChildren;
  const running = step.start && !step.end && !step.isError;
  // ループ検知等、アプリ側の判断で打ち切られたStepは正常完了と区別する
  // （app.py が thinking.metadata.stopped_reason を設定する）。
  const stopped = Boolean(step.metadata?.stopped_reason) && !step.isError;
  const status: keyof typeof STATUS_LABELS = step.isError
    ? 'error'
    : stopped
      ? 'stopped'
      : running
        ? 'running'
        : 'done';
  const typeLabel = TYPE_LABELS[step.type] ?? step.type;
  const displayLabel = step.name && step.name !== typeLabel ? `${typeLabel}: ${step.name}` : typeLabel;

  return (
    <div className={`step-item ${step.isError ? 'step-item--error' : ''}`}>
      <button
        type="button"
        className="step-item-header"
        onClick={() => expandable && setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={`step-item-status ${running ? 'step-item-status--running' : ''}`} />
        <span className="step-item-label">{displayLabel}</span>
        {hasChildren ? <span className="step-item-child-count">{children.length}</span> : null}
        <span className={`step-item-status-badge step-item-status-badge--${status}`}>
          {STATUS_LABELS[status]}
        </span>
        {expandable ? <span className="step-item-caret">{open ? '▾' : '▸'}</span> : null}
      </button>
      {open && hasContent ? (
        <div className="step-item-body">
          {step.input ? (
            <div>
              <div className="step-item-body-title">入力</div>
              <pre>{formatContent(step.input)}</pre>
            </div>
          ) : null}
          {step.output ? (
            <div>
              <div className="step-item-body-title">出力</div>
              <pre>{formatContent(step.output)}</pre>
            </div>
          ) : null}
        </div>
      ) : null}
      {open && hasChildren ? (
        <div className="step-item-children">
          {children.map((child) => (
            <StepItem key={child.id} step={child} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
