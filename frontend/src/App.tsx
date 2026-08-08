import { useEffect } from 'react';
import { useAuth, useChatSession, useChatMessages, useChatData } from '@chainlit/react-client';
import { Header } from './components/Header';
import { LoginForm } from './components/LoginForm';
import { MessagePane } from './components/MessagePane';
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

function App() {
  const { data: authData, isReady: authReady, isAuthenticated } = useAuth();
  const { connect } = useChatSession();
  const { messages } = useChatMessages();
  const { loading } = useChatData();

  const requireLogin = authData?.requireLogin ?? false;
  const canConnect = authReady && (!requireLogin || isAuthenticated);

  useEffect(() => {
    if (!canConnect) return;
    connect({ userEnv: {} });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canConnect]);

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
        <Composer plan={plan} />
      </div>
      <SidePanel sideSteps={displaySideSteps} tokenUsage={tokenUsage} workDir={workDir} plan={plan} />
    </div>
  );
}

export default App;
