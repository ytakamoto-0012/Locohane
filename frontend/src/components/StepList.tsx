import { useEffect, useRef } from 'react';
import type { IStep } from '@chainlit/react-client';
import { StepItem } from './StepItem';

export function StepList({ steps }: { steps: IStep[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // stream_token による Step 内容の更新だけでなく、ユーザーが完了済み
  // StepItem を手動で開閉したときの高さ変化（StepItem 内部の useState で
  // StepList 側は再レンダーされない）にも追従させたいため、依存配列で
  // 更新契機を列挙するのではなく ResizeObserver でコンテナ自身の実高さの
  // 変化を直接監視する。scrollIntoView はドキュメント全体を巻き込んで
  // スクロールさせることがあるため、scrollTop を直接操作する。
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const scrollToBottom = () => {
      el.scrollTop = el.scrollHeight;
    };
    scrollToBottom();
    const ro = new ResizeObserver(scrollToBottom);
    ro.observe(el);
    if (el.firstElementChild) {
      ro.observe(el.firstElementChild);
    }
    return () => ro.disconnect();
  }, [steps]);

  return (
    <div className="step-list-card">
      <div className="step-list-scroll" ref={scrollRef}>
        {steps.length === 0 ? (
          <div className="step-list-empty">ツール呼び出しはまだありません。</div>
        ) : (
          <div className="step-list">
            {steps.map((step) => (
              <StepItem key={step.id} step={step} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
