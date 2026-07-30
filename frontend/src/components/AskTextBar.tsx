import { useEffect, useState, type KeyboardEvent } from 'react';
import { useChatData, useChatInteract } from '@chainlit/react-client';
import type { IStep } from '@chainlit/react-client';

/**
 * AskUserQuestion（labels省略時の自由記述単発質問）専用の表示コンポーネント。
 * backend では cl.AskUserMessage(spec.type === 'text') として届く。
 * 質問文自体は既にアシスタントメッセージとしてスレッドに表示済みのため、
 * ここでは入力欄と送信ボタンのみを提供する。送信は Composer の
 * isReplying 経路と同じ replyMessage を使う（返信経路を増やさない）。
 */
export function AskTextBar() {
  const { askUser } = useChatData();
  const { replyMessage } = useChatInteract();
  const [value, setValue] = useState('');

  const stepId = askUser?.spec.type === 'text' ? askUser.spec.step_id : undefined;

  useEffect(() => {
    setValue('');
  }, [stepId]);

  if (!askUser || askUser.spec.type !== 'text') return null;

  const submit = () => {
    if (!value.trim()) return;
    const message: IStep = {
      threadId: '',
      id: crypto.randomUUID(),
      name: 'あなた',
      type: 'user_message',
      output: value,
      createdAt: new Date().toISOString(),
      metadata: {}
    };
    replyMessage(message);
    setValue('');
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    submit();
  };

  return (
    <form className="ask-text-bar" onSubmit={handleSubmit}>
      <textarea
        className="ask-text-input"
        value={value}
        placeholder="回答を入力..."
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        rows={3}
        autoFocus
      />
      <button type="submit" className="ask-text-submit">
        送信
      </button>
    </form>
  );
}
