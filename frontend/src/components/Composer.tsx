import { useRef, useState, type ClipboardEvent, type KeyboardEvent } from 'react';
import { v4 as uuidv4 } from 'uuid';
import { useChatData, useChatInteract } from '@chainlit/react-client';
import type { IFileRef, IStep } from '@chainlit/react-client';
import { WorkDirButton } from './WorkDirButton';
import { PlanModeBadge } from './PlanModeBadge';
import { Icon } from './Icon';

interface PendingAttachment {
  name: string;
  fileRef?: IFileRef;
  uploading: boolean;
}

export function Composer({ plan, remoteGenerating }: { plan?: IStep; remoteGenerating?: boolean }) {
  const { askUser, disabled, loading } = useChatData();
  const { sendMessage, replyMessage, uploadFile, stopTask } = useChatInteract();
  const [value, setValue] = useState('');
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const isReplying = askUser?.spec.type === 'text';
  // 他セッションでこのスレッドが処理中の間は、このセッションには停止できる
  // タスクが無い（停止ボタンを出しても機能しない）ため、送信自体を止める。
  const inputBlocked = disabled || Boolean(remoteGenerating);

  const handleAttach = (files: FileList | File[] | null) => {
    if (!files) return;
    Array.from(files).forEach((file) => {
      const entry: PendingAttachment = { name: file.name, uploading: true };
      setAttachments((prev) => [...prev, entry]);
      const { promise } = uploadFile(file, () => {});
      promise
        .then((fileRef) => {
          setAttachments((prev) =>
            prev.map((a) => (a === entry ? { ...a, fileRef, uploading: false } : a))
          );
        })
        .catch(() => {
          setAttachments((prev) => prev.filter((a) => a !== entry));
        });
    });
  };

  const handleRemoveAttachment = (index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  };

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const items = Array.from(e.clipboardData?.items ?? []);
    const imageFiles = items
      .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null)
      .map((file, i) => {
        if (file.name && file.name !== 'image.png') return file;
        const ext = file.type.split('/')[1] ?? 'png';
        const renamed = new File([file], `clipboard-${Date.now()}-${i}.${ext}`, { type: file.type });
        return renamed;
      });
    if (imageFiles.length === 0) return;
    e.preventDefault();
    handleAttach(imageFiles);
  };

  const submit = () => {
    if (inputBlocked || (!value.trim() && attachments.length === 0)) return;

    const message: IStep = {
      threadId: '',
      id: uuidv4(),
      name: 'あなた',
      type: 'user_message',
      output: value,
      createdAt: new Date().toISOString(),
      metadata: {}
    };

    if (isReplying && askUser) {
      replyMessage(message);
    } else {
      const fileReferences = attachments.filter((a) => a.fileRef).map((a) => ({ id: a.fileRef!.id }));
      sendMessage(message, fileReferences);
    }

    setValue('');
    setAttachments([]);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer">
      {remoteGenerating ? (
        <div className="composer-remote-generating-banner">
          <span className="composer-remote-generating-dot" />
          他のセッションでこの会話は生成中です。完了すると自動的に読み込み直します…
        </div>
      ) : null}
      {attachments.length > 0 ? (
        <div className="composer-attachments">
          {attachments.map((a, i) => (
            <span key={i} className="composer-attachment-chip">
              {a.uploading ? <span className="attachment-chip-spinner" /> : <Icon name="paperclip" size={12} />}
              {a.name}
              <button
                type="button"
                className="composer-attachment-remove"
                title="添付を削除"
                onClick={() => handleRemoveAttachment(i)}
              >
                <Icon name="x" size={10} />
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <div className="composer-box">
        <textarea
          className="composer-textarea"
          value={value}
          placeholder={
            remoteGenerating ? '他のセッションで生成中です...' : isReplying ? '応答を入力...' : 'メッセージを入力...'
          }
          disabled={inputBlocked}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          rows={4}
        />
        <div className="composer-toolbar">
          <div className="composer-toolbar-left">
            <button
              type="button"
              className="composer-icon-button"
              title="ファイルを添付"
              onClick={() => fileInputRef.current?.click()}
            >
              <Icon name="paperclip" />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              onChange={(e) => handleAttach(e.target.files)}
            />
            <WorkDirButton />
          </div>
          <div className="composer-toolbar-right">
            <PlanModeBadge step={plan} />
            {loading ? (
              <button type="button" className="composer-stop-button" onClick={stopTask}>
                停止
              </button>
            ) : (
              <button type="button" className="composer-submit-button" onClick={submit} disabled={inputBlocked}>
                送信
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
