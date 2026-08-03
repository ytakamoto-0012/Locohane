import { useEffect } from 'react';
import { useAuth, useChatSession, useChatMessages, useChatData } from '@chainlit/react-client';
import { Header } from './components/Header';
import { LoginForm } from './components/LoginForm';
import { MessageThread } from './components/MessageThread';
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
  selectLatestStarters
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
        <div className="messages-scroll">
          <MessageThread messages={mainMessages} showTyping={showTyping} />
        </div>
        <AskActionBar />
        <AskFormBar />
        <AskChoiceFormBar />
        <AskTextBar />
        <AskFileDropzone />
        {mainMessages.length === 0 && <StarterPrompts prompts={starterPrompts} />}
        <Composer plan={plan} />
      </div>
      <SidePanel sideSteps={sideSteps} tokenUsage={tokenUsage} workDir={workDir} plan={plan} />
    </div>
  );
}

export default App;
