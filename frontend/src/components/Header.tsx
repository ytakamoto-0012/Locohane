import { useEffect, useState } from 'react';
import { BACKEND_URL } from '../chainlitClient';
import { ThemePicker } from './ThemePicker';
import { Icon } from './Icon';

const DEFAULT_TITLE = 'Locohane';
const ICON_EXTENSIONS = ['png', 'svg', 'jpg', 'jpeg'];

export function Header() {
  const [title, setTitle] = useState(DEFAULT_TITLE);
  const [iconExtIndex, setIconExtIndex] = useState(0);

  useEffect(() => {
    fetch(`${BACKEND_URL}/public/settings/header.md`)
      .then((res) => (res.ok ? res.text() : Promise.reject()))
      .then((text) => {
        const firstLine = text
          .split('\n')
          .map((line) => line.replace(/^#+\s*/, '').trim())
          .find((line) => line.length > 0);
        if (firstLine) setTitle(firstLine);
      })
      .catch(() => {});
  }, []);

  const handleNewChat = () => {
    if (window.confirm('新しい会話を開始しますか？現在の会話は失われます。')) {
      window.location.reload();
    }
  };

  return (
    <header className="app-header">
      <div className="app-header-brand">
        {iconExtIndex < ICON_EXTENSIONS.length ? (
          <img
            className="app-header-icon"
            src={`${BACKEND_URL}/public/settings/icon.${ICON_EXTENSIONS[iconExtIndex]}`}
            alt=""
            onError={() => setIconExtIndex((index) => index + 1)}
          />
        ) : null}
        <span className="app-header-title">{title}</span>
      </div>
      <div className="app-header-actions">
        <button type="button" className="new-chat-button" title="新規チャット" onClick={handleNewChat}>
          <Icon name="plus" size={14} />
          新規チャット
        </button>
        <ThemePicker />
      </div>
    </header>
  );
}
