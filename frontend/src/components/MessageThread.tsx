import { useContext } from 'react';
import { useRecoilValue } from 'recoil';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChainlitContext, elementState, sessionIdState, type IStep } from '@chainlit/react-client';
import type { IMessageElement } from '@chainlit/react-client';
import { Icon } from './Icon';

function MessageElements({ messageId }: { messageId: string }) {
  const elements = useRecoilValue(elementState);
  const client = useContext(ChainlitContext);
  const sessionId = useRecoilValue(sessionIdState);
  const messageElements = elements.filter(
    (e): e is IMessageElement & { type: 'file' | 'image' } =>
      e.forId === messageId && (e.type === 'file' || e.type === 'image')
  );
  if (messageElements.length === 0) return null;

  const resolveUrl = (e: IMessageElement) =>
    e.url || (e.chainlitKey ? client.getElementUrl(e.chainlitKey, sessionId) : undefined);

  const imageElements = messageElements.filter((e) => e.type === 'image');
  const fileElements = messageElements.filter((e) => e.type === 'file');

  return (
    <>
      {imageElements.length > 0 ? (
        <div className="message-images">
          {imageElements.map((e) => (
            <img key={e.id} src={resolveUrl(e)} alt={e.name} className="message-image" />
          ))}
        </div>
      ) : null}
      {fileElements.length > 0 ? (
        <div className="message-files">
          {fileElements.map((e) => (
            <a key={e.id} href={resolveUrl(e)} download={e.name} className="message-file-link">
              <Icon name="paperclip" size={13} />
              {e.name}
            </a>
          ))}
        </div>
      ) : null}
    </>
  );
}

function MessageBubble({ step }: { step: IStep }) {
  const isUser = step.type === 'user_message';
  return (
    <div className={`message-row ${isUser ? 'message-row--user' : 'message-row--assistant'}`}>
      {!isUser ? <div className="avatar avatar--assistant">AI</div> : null}
      <div className={`message-bubble ${isUser ? 'message-bubble--user' : 'message-bubble--assistant'}`}>
        <div className="message-bubble-content">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />
            }}
          >
            {step.output || ''}
          </ReactMarkdown>
          {step.streaming ? <span className="streaming-cursor" /> : null}
        </div>
        <MessageElements messageId={step.id} />
      </div>
      {isUser ? <div className="avatar avatar--user">You</div> : null}
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="message-row message-row--assistant">
      <div className="avatar avatar--assistant">AI</div>
      <div className="message-bubble message-bubble--assistant message-bubble--typing">
        <span className="typing-dots">
          <span />
          <span />
          <span />
        </span>
      </div>
    </div>
  );
}

export function MessageThread({ messages, showTyping }: { messages: IStep[]; showTyping: boolean }) {
  if (messages.length === 0 && !showTyping) {
    return <div className="message-thread-empty">まだメッセージはありません。下の入力欄から話しかけてください。</div>;
  }

  return (
    <div className="message-thread">
      {messages.map((step) => (
        <MessageBubble key={step.id} step={step} />
      ))}
      {showTyping ? <TypingIndicator /> : null}
    </div>
  );
}
