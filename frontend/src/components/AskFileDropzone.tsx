import { useRef, useState } from 'react';
import { useChatData, useChatInteract } from '@chainlit/react-client';

/** ask_user系のファイル要求(spec.type==='file')に応答するドロップゾーン。 */
export function AskFileDropzone() {
  const { askUser } = useChatData();
  const { uploadFile } = useChatInteract();
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  if (!askUser || askUser.spec.type !== 'file') return null;

  const accept = Array.isArray(askUser.spec.accept) ? askUser.spec.accept.join(',') : undefined;

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0 || uploading) return;
    setUploading(true);
    setProgress(0);

    const fileList = Array.from(files);
    const promises = fileList.map((file, index) => {
      const { promise } = uploadFile(
        file,
        (p) => setProgress((prev) => Math.max(prev, (p + index * 100) / fileList.length)),
        askUser.parentId
      );
      return promise;
    });

    Promise.all(promises)
      .then((fileRefs) => askUser.callback(fileRefs))
      .finally(() => {
        setUploading(false);
        setProgress(0);
        if (inputRef.current) inputRef.current.value = '';
      });
  };

  return (
    <div className="ask-file-dropzone">
      <p>
        ファイルを選択してください
        {askUser.spec.max_size_mb ? `（最大 ${askUser.spec.max_size_mb}MB）` : ''}
      </p>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={(askUser.spec.max_files ?? 1) > 1}
        disabled={uploading}
        onChange={(e) => handleFiles(e.target.files)}
      />
      {uploading ? <span>アップロード中... {Math.round(progress)}%</span> : null}
    </div>
  );
}
