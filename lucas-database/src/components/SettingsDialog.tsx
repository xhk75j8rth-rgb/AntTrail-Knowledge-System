import { Copy, Eye, EyeOff, RefreshCcw, ShieldCheck, X } from 'lucide-react';
import type { ApiTokenStatus } from '../api/lucasDbClient';

interface SettingsDialogProps {
  open: boolean;
  status: ApiTokenStatus | null;
  tokenValue: string;
  showToken: boolean;
  busy: boolean;
  message: string | null;
  exampleText: string;
  onClose: () => void;
  onToggleTokenVisibility: () => void;
  onResetToken: () => void;
  onVerifyToken: () => void;
  onCopyToken: () => void;
  onCopyExample: () => void;
}

const getSourceLabel = (source?: ApiTokenStatus['source']) => {
  switch (source) {
    case 'env':
      return '环境变量';
    case 'database':
      return '本地数据库';
    case 'local_config':
      return '本地默认';
    default:
      return '读取中';
  }
};

export function SettingsDialog({
  open,
  status,
  tokenValue,
  showToken,
  busy,
  message,
  exampleText,
  onClose,
  onToggleTokenVisibility,
  onResetToken,
  onVerifyToken,
  onCopyToken,
  onCopyExample
}: SettingsDialogProps) {
  if (!open) {
    return null;
  }

  const displayValue = showToken
    ? tokenValue || status?.tokenPreview || '未保存到本地'
    : status?.tokenPreview || '未配置';
  const canCopyToken = Boolean(tokenValue);
  const resetDisabled = busy || !status || status.source === 'env';

  return (
    <div className="settings-overlay" role="presentation" onClick={onClose}>
      <section
        className="settings-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="settings-header">
          <div>
            <h2 id="settings-title">设置</h2>
            <p>本地 API 与 API Key 管理</p>
          </div>
          <button className="icon-button" type="button" title="关闭" onClick={onClose}>
            <X size={18} />
          </button>
        </header>

        <div className="settings-grid">
          <section className="settings-section">
            <h3>本地 API</h3>
            <div className="settings-field">
              <span>地址</span>
              <strong>{status?.apiBaseUrl || 'http://localhost:8765'}</strong>
            </div>
            <div className="settings-field">
              <span>状态</span>
              <strong>{status?.configured ? '已配置' : '未配置'}</strong>
            </div>
            <div className="settings-field">
              <span>来源</span>
              <strong>{getSourceLabel(status?.source)}</strong>
            </div>
          </section>

          <section className="settings-section">
            <h3>API Key</h3>
            <label className="settings-token">
              <span>当前 Token</span>
              <div className="settings-token-row">
                <input readOnly value={displayValue} />
                <button
                  className="icon-button"
                  type="button"
                  title={showToken ? '隐藏' : '显示'}
                  onClick={onToggleTokenVisibility}
                >
                  {showToken ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
                <button
                  className="icon-button"
                  type="button"
                  title="复制 API Key"
                  onClick={onCopyToken}
                  disabled={!canCopyToken}
                >
                  <Copy size={16} />
                </button>
                <button
                  className="icon-button"
                  type="button"
                  title="验证 API Key"
                  onClick={onVerifyToken}
                  disabled={busy || !status}
                >
                  <ShieldCheck size={16} />
                </button>
                <button
                  className="icon-button"
                  type="button"
                  title={status?.source === 'env' ? '环境变量锁定' : '生成或重置 API Key'}
                  onClick={onResetToken}
                  disabled={resetDisabled}
                >
                  <RefreshCcw size={16} />
                </button>
              </div>
            </label>
            <p className="settings-note">
              {status?.source === 'env'
                ? '当前 Token 受 LUCAS_DB_API_TOKEN 管理，V0 不能在界面里重置。'
                : status?.source === 'database'
                  ? '当前 Token 只保存了哈希，请重置后再复制明文。'
                  : '可直接生成一个新的本地 API Token；未生成时会使用本地开发默认 Token。'}
            </p>
            {message ? <div className="settings-message">{message}</div> : null}
          </section>
        </div>

        <section className="settings-section settings-example">
          <div className="settings-example-header">
            <h3>Agent 调用示例</h3>
            <button className="text-button" type="button" onClick={onCopyExample}>
              <Copy size={14} />
              复制示例
            </button>
          </div>
          <pre>{exampleText}</pre>
        </section>

        <footer className="settings-footer">
          <button className="text-button" type="button" onClick={onClose}>
            关闭
          </button>
        </footer>
      </section>
    </div>
  );
}
