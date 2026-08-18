import { useEffect, useRef, useState } from 'react';
import { useRecoilValue } from 'recoil';
import {
  useAuth,
  useChatSession,
  useChatInteract,
  useChatMessages,
  useChatData,
  useConfig,
  currentThreadIdState
} from '@chainlit/react-client';
import { BACKEND_URL } from './chainlitClient';
import { Header } from './components/Header';
import { LoginForm } from './components/LoginForm';
import { MessagePane } from './components/MessagePane';
import { Sidebar } from './components/Sidebar';
import { SidePanel } from './components/SidePanel';
import { Composer } from './components/Composer';
import { AskActionBar } from './components/AskActionBar';
import { AskFormBar } from './components/AskFormBar';
import { AskChoiceFormBar } from './components/AskChoiceFormBar';
import { AskTextBar } from './components/AskTextBar';
import { AskFileDropzone } from './components/AskFileDropzone';
import { StarterPrompts } from './components/StarterPrompts';
import {
  selectMainThread,
  selectSideSteps,
  selectLatestTokenUsage,
  selectLatestWorkDir,
  selectLatestPlan,
  selectLatestStarters,
  selectLatestMaxDisplayMessages,
  selectLatestMaxDisplaySideSteps
} from './utils/messageTree';
import './styles.css';

// 他セッション（別スレッドを開いた同じタブ・別タブ）が裏で処理中かどうかを
// 知るライブpushの仕組みが無いため、ポーリングで代替する（app.pyの
// _generating_thread_ids/on_message、/locohane/threads/{id}/status参照）。
const REMOTE_GENERATING_POLL_INTERVAL_MS = 3000;

function App() {
  const { data: authData, isReady: authReady, isAuthenticated } = useAuth();
  const { connect } = useChatSession();
  const { setIdToResume } = useChatInteract();
  const { messages } = useChatMessages();
  const { loading } = useChatData();
  const { config } = useConfig();
  const currentThreadId = useRecoilValue(currentThreadIdState);

  const requireLogin = authData?.requireLogin ?? false;
  const canConnect = authReady && (!requireLogin || isAuthenticated);

  // ?thread=<id> があれば再開対象としてrecoil状態へ書き込んでからconnectする。
  // useChatSession().connect は threadIdToResumeState を読むdebounce関数のため、
  // setIdToResume 直後に同期的にconnectするとReactのstate反映タイミングと
  // 競合し古い値（未指定）が送られるリスクがある。seeded で「recoilへの
  // 書き込み確定後にのみconnectする」ことを保証する（Sidebar.tsx参照）。
  const [seeded, setSeeded] = useState(false);
  useEffect(() => {
    const threadId = new URLSearchParams(window.location.search).get('thread');
    if (threadId) setIdToResume(threadId);
    setSeeded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!canConnect || !seeded) return;
    connect({ userEnv: {} });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canConnect, seeded]);

  // 生成中に別スレッドへ移動すると、元のセッションはソケット切断され
  // ライブストリーミングが届かなくなる（バックエンド側の処理自体は
  // on_chat_end 経由でバックグラウンド継続するがpushする相手がいない）。
  // そのまま何も表示しないと「会話が終了したように見える」ため、開いている
  // スレッドが他セッションで処理中かをポーリングし、入力欄を無効化した上で
  // 完了を検知したら再読み込みして確定内容を取り込む。
  const [remoteGenerating, setRemoteGenerating] = useState(false);
  const wasRemoteGeneratingRef = useRef(false);
  const loadingRef = useRef(loading);
  useEffect(() => {
    loadingRef.current = loading;
  }, [loading]);

  useEffect(() => {
    wasRemoteGeneratingRef.current = false;
    setRemoteGenerating(false);
    if (!currentThreadId || !config?.dataPersistence) return;

    let cancelled = false;
    const poll = () => {
      fetch(`${BACKEND_URL}/locohane/threads/${currentThreadId}/status`, { credentials: 'include' })
        .then((res) => (res.ok ? res.json() : Promise.reject()))
        .then((data: { isGenerating: boolean }) => {
          if (cancelled) return;
          // 自分自身がまさに送信中のターンは loading 側で表現される。ここで
          // 除外しないと、自分の送信完了直後に強制リロードがかかってしまう。
          const remote = data.isGenerating && !loadingRef.current;
          if (remote) {
            wasRemoteGeneratingRef.current = true;
            setRemoteGenerating(true);
            return;
          }
          if (wasRemoteGeneratingRef.current) {
            window.location.reload();
            return;
          }
          setRemoteGenerating(false);
        })
        .catch(() => {});
    };
    poll();
    const timer = setInterval(poll, REMOTE_GENERATING_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [currentThreadId, config?.dataPersistence]);

  const mainMessages = selectMainThread(messages);
  const sideSteps = selectSideSteps(messages);
  const tokenUsage = selectLatestTokenUsage(messages);
  const workDir = selectLatestWorkDir(messages);
  const plan = selectLatestPlan(messages);
  const starterPrompts = selectLatestStarters(messages);

  // 表示専用の間引き。会話コンテキスト(LangGraph側のstate)やログには一切影響しない。
  // 上限0または未取得（値未着信/パース失敗）の場合は無制限（従来どおり全件描画）。
  const maxDisplayMessages = selectLatestMaxDisplayMessages(messages);
  const displayMessages =
    maxDisplayMessages && maxDisplayMessages > 0
      ? mainMessages.slice(-maxDisplayMessages)
      : mainMessages;

  // サイドパネル（Step一覧）も同様に表示専用の間引きを適用する。
  const maxDisplaySideSteps = selectLatestMaxDisplaySideSteps(messages);
  const displaySideSteps =
    maxDisplaySideSteps && maxDisplaySideSteps > 0
      ? sideSteps.slice(-maxDisplaySideSteps)
      : sideSteps;

  const lastMain = mainMessages[mainMessages.length - 1];
  // 送信直後〜最終回答のストリーミング開始までの「空白時間」を可視化する。
  // 既に回答トークンが届き始めていれば streaming カーソル側で表現されるため不要。
  const showTyping = loading && !(lastMain?.type === 'assistant_message' && lastMain.streaming);

  if (!authReady) return null;
  if (requireLogin && !isAuthenticated) return <LoginForm />;

  return (
    <div className="app-shell">
      <Sidebar />
      <div className="main-column">
        <Header />
        <MessagePane messages={displayMessages} showTyping={showTyping} />
        <AskActionBar />
        <AskFormBar />
        <AskChoiceFormBar />
        <AskTextBar />
        <AskFileDropzone />
        {!mainMessages.some((m) => m.type === 'user_message') && (
          <StarterPrompts prompts={starterPrompts} />
        )}
        <Composer plan={plan} remoteGenerating={remoteGenerating} />
      </div>
      <SidePanel sideSteps={displaySideSteps} tokenUsage={tokenUsage} workDir={workDir} plan={plan} />
    </div>
  );
}

export default App;
