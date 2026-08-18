import { useCallback, useEffect, useState, type MouseEvent } from 'react';
import { useRecoilValue } from 'recoil';
import { currentThreadIdState, firstUserInteraction, sessionIdState, useConfig } from '@chainlit/react-client';
import { BACKEND_URL } from '../chainlitClient';
import { Icon } from './Icon';

interface ThreadSummary {
  id: string;
  name: string | null;
  updatedAt: string;
  isGenerating: boolean;
}

// 他のセッション（別スレッドを開いた同じタブ・別タブ）が裏で処理中かどうかを
// 知るライブpushの仕組みが無いため、ポーリングで代替する（app.pyの
// _generating_thread_ids/on_message参照）。
const GENERATING_POLL_INTERVAL_MS = 3000;

/** ?thread=<id> の付け外しだけを行い、リロードで新規/再開を切り替える。
 * useChatSession().connect は threadIdToResumeState を読むdebounce関数のため、
 * setIdToResume 直後に同期的に connect し直すとReactのstate反映タイミングと
 * 競合しうる（App.tsx参照）。URL+リロードなら常にマウント時の1回読みで確定する。 */
function goToThread(threadId: string | null) {
  const url = new URL(window.location.href);
  if (threadId) {
    url.searchParams.set('thread', threadId);
  } else {
    url.searchParams.delete('thread');
  }
  window.location.href = url.pathname + url.search;
}

export function Sidebar() {
  const { config } = useConfig();
  const currentThreadId = useRecoilValue(currentThreadIdState);
  const firstInteraction = useRecoilValue(firstUserInteraction);
  const sessionId = useRecoilValue(sessionIdState);
  const [collapsed, setCollapsed] = useState(false);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);

  const refresh = useCallback(() => {
    // session_id を渡すと、自分自身が生成中のスレッドにはパルスドットを
    // 出さない（app.py の /locohane/threads のdocstring参照）。
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    fetch(`${BACKEND_URL}/locohane/threads${query}`, { credentials: 'include' })
      .then((res) => (res.ok ? res.json() : Promise.reject()))
      .then((data) => setThreads(data.threads ?? []))
      .catch(() => {});
  }, [sessionId]);

  // マウント時、および新規スレッドがChainlit本体により命名された直後
  // （firstUserInteractionが変化するタイミング）に再取得する。
  useEffect(() => {
    refresh();
  }, [refresh, firstInteraction]);

  // 「生成中」インジケーターは他セッションの状態のためライブpushできず、
  // ポーリングで更新する（config取得前でも[thread_store]有効なら回してよい
  // ため、後述のearly returnより前に置く）。
  useEffect(() => {
    if (!config?.dataPersistence) return;
    const timer = setInterval(refresh, GENERATING_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refresh, config?.dataPersistence]);

  // [thread_store].enabled=false（データレイヤー未登録）なら何も描画しない。
  if (!config?.dataPersistence) return null;

  if (collapsed) {
    return (
      <button
        type="button"
        className="thread-sidebar-collapsed"
        title="会話一覧を開く"
        onClick={() => setCollapsed(false)}
      >
        <Icon name="panel-left" size={14} />
      </button>
    );
  }

  const handleRename = async (thread: ThreadSummary, event: MouseEvent) => {
    event.stopPropagation();
    const name = window.prompt('会話の名前を変更', thread.name ?? '');
    if (!name || name === thread.name) return;
    await fetch(`${BACKEND_URL}/locohane/threads/${thread.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ name })
    });
    refresh();
  };

  const handleDelete = async (thread: ThreadSummary, event: MouseEvent) => {
    event.stopPropagation();
    if (!window.confirm(`「${thread.name ?? '無題の会話'}」を削除しますか？`)) return;
    await fetch(`${BACKEND_URL}/locohane/threads/${thread.id}`, {
      method: 'DELETE',
      credentials: 'include'
    });
    if (thread.id === currentThreadId) {
      // 開いている会話を削除した場合、次のメッセージで空スタブが復活しないよう
      // 新規チャットへフォールバックする。
      goToThread(null);
      return;
    }
    refresh();
  };

  return (
    <aside className="thread-sidebar">
      <div className="thread-sidebar-header">
        <button type="button" className="new-chat-button" onClick={() => goToThread(null)}>
          <Icon name="plus" size={14} />
          新規チャット
        </button>
        <button
          type="button"
          className="thread-sidebar-toggle"
          title="サイドバーを折りたたむ"
          onClick={() => setCollapsed(true)}
        >
          <Icon name="panel-left" size={14} />
        </button>
      </div>
      <ul className="thread-list">
        {threads.map((thread) => (
          <li
            key={thread.id}
            className={
              thread.id === currentThreadId ? 'thread-list-item thread-list-item-active' : 'thread-list-item'
            }
            onClick={() => goToThread(thread.id)}
          >
            {thread.isGenerating && (
              <span className="thread-list-item-generating" title="生成中" aria-label="生成中" />
            )}
            <span className="thread-list-item-name">{thread.name || '無題の会話'}</span>
            <span className="thread-list-item-actions">
              <button type="button" title="名前を変更" onClick={(event) => handleRename(thread, event)}>
                <Icon name="pencil" size={12} />
              </button>
              <button type="button" title="削除" onClick={(event) => handleDelete(thread, event)}>
                <Icon name="trash" size={12} />
              </button>
            </span>
          </li>
        ))}
      </ul>
    </aside>
  );
}
