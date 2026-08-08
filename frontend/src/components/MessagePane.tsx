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
  // 参照し続けるため、最新値は ref 経由で読む。
  const autoScrollRef = useRef(true);
  const lastMessageIdRef = useRef<string | null>(null);

  useEffect(() => {
    autoScrollRef.current = autoScroll;
  }, [autoScroll]);

  // 新しいユーザーメッセージが追加されたら、直前にオートスクロールを
  // 解除していても最新のやり取りへ強制的に追従を再開する。
  useEffect(() => {
    const last = messages[messages.length - 1];
    if (last && last.id !== lastMessageIdRef.current && last.type === 'user_message') {
      setAutoScroll(true);
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
    setAutoScroll(distanceFromBottom <= BOTTOM_THRESHOLD_PX);
  };

  const handleScrollToBottomClick = () => {
    const el = containerRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    setAutoScroll(true);
  };

  return (
    <div className="messages-scroll-wrapper">
      <div className="messages-scroll" ref={containerRef} onScroll={handleScroll}>
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
