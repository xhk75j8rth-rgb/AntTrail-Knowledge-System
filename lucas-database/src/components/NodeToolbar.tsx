import { FileText, Network, Plus, RefreshCcw, Save, Trash2 } from 'lucide-react';

export type WorkspaceViewMode = 'edit' | 'graph';

interface NodeToolbarProps {
  selectedNodeId: string | null;
  saveStatus: 'idle' | 'saving' | 'saved' | 'error';
  viewMode?: WorkspaceViewMode;
  onCreateRoot: () => void;
  onCreateChild: () => void;
  onSave: () => void;
  onDelete: () => void;
  onRefresh: () => void;
  onViewModeChange?: (mode: WorkspaceViewMode) => void;
}

const statusLabel = {
  idle: '未修改',
  saving: '保存中...',
  saved: '已保存',
  error: '保存失败'
};

export function NodeToolbar({
  selectedNodeId,
  saveStatus,
  viewMode,
  onCreateRoot,
  onCreateChild,
  onSave,
  onDelete,
  onRefresh,
  onViewModeChange
}: NodeToolbarProps) {
  return (
    <div className="node-toolbar" aria-label="节点工具栏">
      <button className="text-button primary" type="button" onClick={onCreateRoot}>
        <Plus size={16} />
        根节点
      </button>
      <button
        className="text-button"
        type="button"
        onClick={onCreateChild}
        disabled={!selectedNodeId}
      >
        <Plus size={16} />
        子节点
      </button>
      <button
        className="icon-button"
        type="button"
        title="保存"
        onClick={onSave}
        disabled={!selectedNodeId}
      >
        <Save size={17} />
      </button>
      <button
        className="icon-button"
        type="button"
        title="删除"
        onClick={onDelete}
        disabled={!selectedNodeId}
      >
        <Trash2 size={17} />
      </button>
      <button className="icon-button" type="button" title="刷新" onClick={onRefresh}>
        <RefreshCcw size={17} />
      </button>
      {viewMode && onViewModeChange ? (
        <div className="view-switcher" aria-label="视图切换">
          <button
            className={viewMode === 'edit' ? 'active' : ''}
            type="button"
            onClick={() => onViewModeChange('edit')}
          >
            <FileText size={15} />
            内容
          </button>
          <button
            className={viewMode === 'graph' ? 'active' : ''}
            type="button"
            onClick={() => onViewModeChange('graph')}
          >
            <Network size={15} />
            图谱
          </button>
        </div>
      ) : null}
      <span className={`save-status ${saveStatus}`}>{statusLabel[saveStatus]}</span>
    </div>
  );
}
