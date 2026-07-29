import { useContext, useState } from 'react';
import { useRecoilValue } from 'recoil';
import { ChainlitContext, sessionIdState, type IAction } from '@chainlit/react-client';
import { Icon } from './Icon';

/** ツールバーの「作業フォルダ」アイコン。app.py の @cl.action_callback("pick_work_dir") を呼ぶ。 */
export function WorkDirButton() {
  const client = useContext(ChainlitContext);
  const sessionId = useRecoilValue(sessionIdState);
  const [pending, setPending] = useState(false);

  const handleClick = async () => {
    if (pending) return;
    setPending(true);
    const action: IAction = {
      id: '',
      name: 'pick_work_dir',
      payload: {},
      label: '',
      tooltip: '',
      forId: '',
      onClick: () => {}
    };
    try {
      await client.callAction(action, sessionId);
    } finally {
      setPending(false);
    }
  };

  return (
    <button
      type="button"
      className="work-dir-button"
      title="作業フォルダを選択"
      onClick={handleClick}
      disabled={pending}
    >
      <Icon name="folder" />
    </button>
  );
}
