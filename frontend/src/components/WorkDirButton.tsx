import { useContext, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useRecoilValue } from 'recoil';
import { ChainlitContext, sessionIdState, type IAction } from '@chainlit/react-client';
import { Icon } from './Icon';

/**
 * ツールバーの「作業フォルダ」アイコン。app.py の @cl.action_callback("pick_work_dir") を呼ぶ。
 *
 * OSネイティブのフォルダ選択ダイアログはバックエンド側（サーバーPC）にしか
 * 表示できず、サーバーとブラウザが別マシンの構成だと気づかれずクライアントが
 * 固まって見える問題があったため、パス文字列をこのポップオーバーで直接
 * 入力させる方式にしている（2026-08-19 ユーザー報告）。
 */
export function WorkDirButton() {
  const client = useContext(ChainlitContext);
  const sessionId = useRecoilValue(sessionIdState);
  const [pending, setPending] = useState(false);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const onPointerDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  const submit = async (path: string) => {
    if (pending) return;
    setPending(true);
    const action: IAction = {
      id: '',
      name: 'pick_work_dir',
      payload: { path },
      label: '',
      tooltip: '',
      forId: '',
      onClick: () => {}
    };
    try {
      await client.callAction(action, sessionId);
      setOpen(false);
      setValue('');
    } finally {
      setPending(false);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      submit(value.trim());
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
    }
  };

  return (
    <div className="work-dir-picker" ref={containerRef}>
      <button
        type="button"
        className="work-dir-button"
        title="作業フォルダを設定"
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="folder" />
      </button>
      {open && (
        <div className="work-dir-popover">
          <input
            ref={inputRef}
            type="text"
            className="work-dir-popover-input"
            value={value}
            placeholder={'作業フォルダの絶対パス（例: C:\\Users\\you\\project）'}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <div className="work-dir-popover-actions">
            <button
              type="button"
              className="work-dir-popover-reset"
              onClick={() => submit('')}
              disabled={pending}
              title="config.ini の既定フォルダに戻す"
            >
              既定値に戻す
            </button>
            <div className="work-dir-popover-actions-right">
              <button type="button" className="work-dir-popover-cancel" onClick={() => setOpen(false)}>
                キャンセル
              </button>
              <button
                type="button"
                className="work-dir-popover-submit"
                onClick={() => submit(value.trim())}
                disabled={pending || !value.trim()}
              >
                設定
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
