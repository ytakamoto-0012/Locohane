import { useEffect, useRef, useState } from 'react';
import type { IStep } from '@chainlit/react-client';
import { MessageThread } from './MessageThread';
import { Icon } from './Icon';

// 最下部からこの距離(px)以内であれば「最下部にいる」とみなす。
const BOTTOM_THRESHOLD_PX = 64;

export function MessagePane({ messages, showTyping }: { messages: IStep[]; showTyping: boolean }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  // ResizeObserver のコールバックや scroll ハンドラは古いクロージャを
  // 参照し続けるため、最新値は ref 経由で読む。ref の更新を useEffect
  // (レンダー確定後)に任せると、その反映前に stream_token による
  // ResizeObserver コールバックが割り込んで古い値のまま強制スナップして
  // しまうことがあるため、setAutoScrollBoth で state と同時に同期更新する。
  const autoScrollRef = useRef(true);
  const lastMessageIdRef = useRef<string | null>(null);

  const setAutoScrollBoth = (value: boolean) => {
    autoScrollRef.current = value;
    setAutoScroll(value);
  };

  // 新しいユーザーメッセージが追加されたら、直前にオートスクロールを
  // 解除していても最新のやり取りへ強制的に追従を再開する。
  useEffect(() => {
    const last = messages[messages.length - 1];
    if (last && last.id !== lastMessageIdRef.current && last.type === 'user_message') {
      setAutoScrollBoth(true);
    }
    lastMessageIdRef.current = last?.id ?? null;
  }, [messages]);

  // stream_token によるコンテンツの高さ変化に追従する。scrollIntoView は
  // ドキュメント全体を巻き込むことがあるため scrollTop を直接操作する
  // (StepList.tsx と同じ方式)。
  useEffect(() => {
    const el = containerRef.current;
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
  }, [messages, showTyping]);

  const handleScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setAutoScrollBoth(distanceFromBottom <= BOTTOM_THRESHOLD_PX);
  };

  // 上方向へのホイール操作があった時点で即座にオートスクロールを解除する。
  // scroll イベントの distanceFromBottom 判定だけだと、ストリーミング中の
  // ResizeObserver による強制スナップ(下記 useEffect)と競合し、閾値を
  // 超えるまで押し戻されてしまうため、ユーザー操作を最優先で反映する。
  const handleWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    if (e.deltaY < 0) {
      setAutoScrollBoth(false);
    }
  };

  const handleScrollToBottomClick = () => {
    const el = containerRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    setAutoScrollBoth(true);
  };

  return (
    <div className="messages-scroll-wrapper">
      <div className="messages-scroll" ref={containerRef} onScroll={handleScroll} onWheel={handleWheel}>
        <MessageThread messages={messages} showTyping={showTyping} />
      </div>
      {!autoScroll ? (
        <button
          type="button"
          className="scroll-to-bottom-button"
          onClick={handleScrollToBottomClick}
          aria-label="最新のメッセージへスクロール"
          title="最新のメッセージへ"
        >
          <Icon name="arrow-down" size={16} />
        </button>
      ) : null}
    </div>
  );
}
