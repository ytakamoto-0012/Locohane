import { useEffect, useRef, useState } from 'react';
import type { IStep } from '@chainlit/react-client';
import { StepItem } from './StepItem';
import { Icon } from './Icon';

// 最下部からこの距離(px)以内であれば「最下部にいる」とみなす。MessagePane.tsx と同じ値。
const BOTTOM_THRESHOLD_PX = 64;

export function StepList({ steps }: { steps: IStep[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  // ResizeObserver のコールバックや scroll ハンドラは古いクロージャを
  // 参照し続けるため、最新値は ref 経由で読む。ref の更新を useEffect
  // (レンダー確定後)に任せると、その反映前に stream_token による
  // ResizeObserver コールバックが割り込んで古い値のまま強制スナップして
  // しまうことがあるため、setAutoScrollBoth で state と同時に同期更新する
  // (MessagePane.tsx と同じ方式)。
  const autoScrollRef = useRef(true);

  const setAutoScrollBoth = (value: boolean) => {
    autoScrollRef.current = value;
    setAutoScroll(value);
  };

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
      if (!autoScrollRef.current) return;
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

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setAutoScrollBoth(distanceFromBottom <= BOTTOM_THRESHOLD_PX);
  };

  // 上方向へのホイール操作があった時点で即座にオートスクロールを解除する。
  // scroll イベントの distanceFromBottom 判定だけだと、ストリーミング中の
  // ResizeObserver による強制スナップ(下記 useEffect)と競合し、閾値を
  // 超えるまで押し戻されてしまうため、ユーザー操作を最優先で反映する
  // (MessagePane.tsx と同じ方式)。
  const handleWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    if (e.deltaY < 0) {
      setAutoScrollBoth(false);
    }
  };

  const handleScrollToBottomClick = () => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    setAutoScrollBoth(true);
  };

  return (
    <div className="step-list-card">
      <div className="step-list-scroll-wrapper">
        <div className="step-list-scroll" ref={scrollRef} onScroll={handleScroll} onWheel={handleWheel}>
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
        {!autoScroll ? (
          <button
            type="button"
            className="scroll-to-bottom-button"
            onClick={handleScrollToBottomClick}
            aria-label="最新のStepへスクロール"
            title="最新のStepへ"
          >
            <Icon name="arrow-down" size={16} />
          </button>
        ) : null}
      </div>
    </div>
  );
}
