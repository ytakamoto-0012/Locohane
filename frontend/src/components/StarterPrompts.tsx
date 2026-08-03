import { useChatInteract } from '@chainlit/react-client';
import type { IStep } from '@chainlit/react-client';

/**
 * チャット開始時のみ表示する定型文ボタン列。クリックすると即座にそのテキストを
 * ユーザー発言として送信する（Composer.tsx の submit() と同じ IStep 組み立て方式）。
 */
export function StarterPrompts({ prompts }: { prompts: string[] }) {
  const { sendMessage } = useChatInteract();

  if (prompts.length === 0) return null;

  const handleClick = (text: string) => {
    const message: IStep = {
      threadId: '',
      id: crypto.randomUUID(),
      name: 'あなた',
      type: 'user_message',
      output: text,
      createdAt: new Date().toISOString(),
      metadata: {}
    };
    sendMessage(message, []);
  };

  return (
    <div className="starter-prompts">
      {prompts.map((text, i) => (
        <button
          key={i}
          type="button"
          className="starter-prompt-button"
          onClick={() => handleClick(text)}
        >
          {text}
        </button>
      ))}
    </div>
  );
}
