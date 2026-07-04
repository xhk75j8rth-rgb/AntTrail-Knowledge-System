import { File as FileIcon, Image, Paperclip, Trash2, Upload, Video } from 'lucide-react';
import { useRef } from 'react';
import { lucasDbClient, type NodeAttachment } from '../api/lucasDbClient';
import { MarkdownPreview } from './MarkdownPreview';

interface ContentEditorProps {
  title: string;
  content: string;
  attachments?: NodeAttachment[];
  previewMarkdown?: string | null;
  disabled?: boolean;
  uploading?: boolean;
  onTitleChange: (value: string) => void;
  onContentChange: (value: string) => void;
  onUploadAttachments: (files: File[]) => void;
  onDeleteAttachment: (id: string) => void;
}

const formatFileSize = (bytes: number) => {
  if (bytes < 1024) {
    return `${bytes} B`;
  }

  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unitIndex]}`;
};

const iconByKind = {
  image: Image,
  video: Video,
  file: FileIcon
};

export function ContentEditor({
  title,
  content,
  attachments = [],
  previewMarkdown,
  disabled,
  uploading,
  onTitleChange,
  onContentChange,
  onUploadAttachments,
  onDeleteAttachment
}: ContentEditorProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  if (disabled) {
    return (
      <div className="editor-empty">
        <h2>选择或创建一个节点</h2>
        <p>每个节点都可以保存内容，也可以继续创建子节点。</p>
      </div>
    );
  }

  return (
    <section className="content-editor" aria-label="内容编辑器">
      <div className="attachment-toolbar">
        <button
          className="text-button"
          type="button"
          disabled={uploading}
          onClick={() => fileInputRef.current?.click()}
        >
          <Upload size={15} />
          {uploading ? '上传中' : '上传文件'}
        </button>
        <span>
          <Paperclip size={14} />
          {attachments.length} 个附件
        </span>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="attachment-input"
          onChange={(event) => {
            const files = Array.from(event.target.files || []);
            if (files.length > 0) {
              onUploadAttachments(files);
            }
            event.target.value = '';
          }}
        />
      </div>

      {attachments.length > 0 ? (
        <div className="attachment-grid" aria-label="节点附件">
          {attachments.map((attachment) => {
            const Icon = iconByKind[attachment.kind];
            const url = lucasDbClient.getAttachmentUrl(attachment);

            return (
              <article
                className={`attachment-item attachment-item-${attachment.kind}`}
                key={attachment.id}
              >
                <div className="attachment-preview">
                  {attachment.kind === 'image' ? (
                    <img src={url} alt={attachment.original_name} />
                  ) : attachment.kind === 'video' ? (
                    <video src={url} controls preload="metadata" />
                  ) : (
                    <Icon size={30} />
                  )}
                </div>
                <div className="attachment-meta">
                  <a href={url} target="_blank" rel="noreferrer">
                    {attachment.original_name}
                  </a>
                  <span>
                    {attachment.mime_type || 'application/octet-stream'} ·{' '}
                    {formatFileSize(attachment.size_bytes)}
                  </span>
                </div>
                <button
                  className="icon-button small"
                  type="button"
                  aria-label={`删除附件 ${attachment.original_name}`}
                  title="删除附件"
                  onClick={() => onDeleteAttachment(attachment.id)}
                >
                  <Trash2 size={14} />
                </button>
              </article>
            );
          })}
        </div>
      ) : null}

      {previewMarkdown ? (
        <MarkdownPreview markdown={previewMarkdown} />
      ) : (
        <>
          <input
            className="title-input"
            value={title}
            onChange={(event) => onTitleChange(event.target.value)}
            placeholder="节点标题"
          />
          <textarea
            className="content-textarea"
            value={content}
            onChange={(event) => onContentChange(event.target.value)}
            placeholder="在这里写内容备注或派生文本..."
            spellCheck={false}
          />
        </>
      )}
    </section>
  );
}
