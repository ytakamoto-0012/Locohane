import { useEffect, useState } from 'react';
import { useChatData } from '@chainlit/react-client';

interface IMultiChoiceFormProps {
  question?: string;
  choices?: string[];
}

/**
 * ask_user_choice（multi_select=True 指定時）専用の表示コンポーネント。
 * backend では cl.AskElementMessage(spec.type === 'element') として届くため、
 * askUser.spec.element_id に一致するカスタム要素の props（question/choices）を
 * 元にチェックボックス一覧を描画し、送信時に askUser.callback へ
 * { submitted: true, values: 選択された選択肢の文字列配列 } を返す。
 * AskFormBar（labels指定の自由記述フォーム）と同じ askUser.spec.type
 * ('element') を使うが、props に labels が無いため AskFormBar 側は
 * 何も描画しない（互いの props 形状で排他になる）。
 */
export function AskChoiceFormBar() {
  const { askUser, elements } = useChatData();

  const element =
    askUser?.spec.type === 'element'
      ? elements.find((e) => e.type === 'custom' && e.id === askUser.spec.element_id)
      : undefined;
  const props = element?.type === 'custom' ? (element.props as IMultiChoiceFormProps) : undefined;
  const choices = props?.choices ?? [];

  const [checked, setChecked] = useState<boolean[]>(() => choices.map(() => false));

  useEffect(() => {
    setChecked(choices.map(() => false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [element?.id]);

  if (!askUser || askUser.spec.type !== 'element' || !element || choices.length === 0) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const values = choices.filter((_, i) => checked[i]);
    askUser.callback({ submitted: true, values });
  };

  return (
    <form className="ask-form-bar" onSubmit={handleSubmit}>
      {props?.question && <div className="ask-form-question">{props.question}</div>}
      {choices.map((choice, i) => (
        <label key={i} className="ask-choice-field">
          <input
            type="checkbox"
            checked={checked[i] ?? false}
            onChange={(e) => {
              const next = [...checked];
              next[i] = e.target.checked;
              setChecked(next);
            }}
          />
          <span>{choice}</span>
        </label>
      ))}
      <button type="submit" className="ask-form-submit">
        送信
      </button>
    </form>
  );
}
