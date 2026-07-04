import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, PanelLeftClose, Settings } from 'lucide-react';
import {
  lucasDbClient,
  type ApiTokenStatus,
  type NodeDetail,
  type NodeCardSummary,
  type SearchResult,
  type TreeNode
} from '../api/lucasDbClient';
import { ContentEditor } from '../components/ContentEditor';
import { GraphView } from '../components/GraphView';
import { NodeCardPanel } from '../components/NodeCardPanel';
import { NodeToolbar, type WorkspaceViewMode } from '../components/NodeToolbar';
import { NodeTree } from '../components/NodeTree';
import { SearchBox } from '../components/SearchBox';
import { SettingsDialog } from '../components/SettingsDialog';
import antTrailMark from '../assets/anttrail-mark.png';

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error';

const CHAT_UI_URL = 'http://127.0.0.1:3963/ui';

const apiTokenSourceLabel: Record<ApiTokenStatus['source'], string> = {
  env: '环境变量',
  local_config: '本地默认',
  database: '本地数据库'
};

const findFirstNode = (nodes: TreeNode[]): TreeNode | null => {
  for (const node of nodes) {
    return node;
  }
  return null;
};

const collectAncestorIds = (nodes: TreeNode[], targetId: string, trail: string[] = []): string[] => {
  for (const node of nodes) {
    const nextTrail = [...trail, node.id];
    if (node.id === targetId) {
      return trail;
    }
    const childTrail = collectAncestorIds(node.children, targetId, nextTrail);
    if (childTrail.length > 0) {
      return childTrail;
    }
  }

  return [];
};

export function WorkspacePage() {
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [node, setNode] = useState<NodeDetail | null>(null);
  const [draftTitle, setDraftTitle] = useState('');
  const [draftContent, setDraftContent] = useState('');
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [apiTokenStatus, setApiTokenStatus] = useState<ApiTokenStatus | null>(null);
  const [apiTokenValue, setApiTokenValue] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [settingsMessage, setSettingsMessage] = useState<string | null>(null);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [nodeCards, setNodeCards] = useState<NodeCardSummary[]>([]);
  const [viewMode, setViewMode] = useState<WorkspaceViewMode>('edit');
  const [uploadingAttachments, setUploadingAttachments] = useState(false);
  const saveTimer = useRef<number | null>(null);
  const latestDraft = useRef({ title: '', content: '' });
  const syncTokenValueFromStatus = useCallback((status: ApiTokenStatus) => {
    localStorage.setItem('lucas_db_api_token_source', status.source);

    if (window.lucasDbConfig?.apiTokenSource === 'env') {
      setApiTokenValue(window.lucasDbConfig.apiToken || '');
      return;
    }

    if (status.source === 'env') {
      localStorage.removeItem('lucas_db_api_token');
      setApiTokenValue(window.lucasDbConfig?.apiToken || import.meta.env.VITE_LUCAS_DB_API_TOKEN || '');
      return;
    }

    if (status.source === 'database') {
      setApiTokenValue(localStorage.getItem('lucas_db_api_token') || '');
      return;
    }

    localStorage.removeItem('lucas_db_api_token');
    setApiTokenValue('');
  }, []);

  const loadApiTokenStatus = useCallback(async () => {
    setSettingsBusy(true);
    try {
      const status = await lucasDbClient.getApiTokenStatus();
      setApiTokenStatus(status);
      syncTokenValueFromStatus(status);
    } catch (error) {
      setSettingsMessage(error instanceof Error ? error.message : '读取设置失败');
    } finally {
      setSettingsBusy(false);
    }
  }, [syncTokenValueFromStatus]);

  const refreshTree = useCallback(async () => {
    const nextTree = await lucasDbClient.getTree();
    setTree(nextTree);
    setExpandedIds((current) => {
      const next = new Set(current);
      nextTree.forEach((root) => next.add(root.id));
      return next;
    });

    if (!selectedNodeId) {
      const first = findFirstNode(nextTree);
      if (first) {
        setSelectedNodeId(first.id);
      }
    }

    return nextTree;
  }, [selectedNodeId]);

  const loadNode = useCallback(async (id: string) => {
    const detail = await lucasDbClient.getNode(id);
    setNode(detail);
    setDraftTitle(detail.title);
    setDraftContent(detail.content);
    latestDraft.current = { title: detail.title, content: detail.content };
    setSaveStatus('saved');
  }, []);

  const loadNodeCards = useCallback(async (id: string) => {
    const result = await lucasDbClient.getNodeCards(id);
    setNodeCards(result.cards);
  }, []);

  useEffect(() => {
    refreshTree().catch((error) => setLoadError(error.message));
  }, [refreshTree]);

  useEffect(() => {
    if (settingsOpen) {
      void loadApiTokenStatus();
    }
  }, [settingsOpen, loadApiTokenStatus]);

  useEffect(() => {
    if (!selectedNodeId) {
      setNode(null);
      setNodeCards([]);
      return;
    }

    loadNode(selectedNodeId).catch((error) => {
      setLoadError(error.message);
      setSaveStatus('error');
    });
    loadNodeCards(selectedNodeId).catch((error) => setLoadError(error.message));
  }, [selectedNodeId, loadNode, loadNodeCards]);

  const selectedPathAncestors = useMemo(
    () => (selectedNodeId ? collectAncestorIds(tree, selectedNodeId) : []),
    [selectedNodeId, tree]
  );
  const selectedMarkdownView = useMemo(
    () => nodeCards.find((card) => card.markdown_view)?.markdown_view || null,
    [nodeCards]
  );

  useEffect(() => {
    if (selectedPathAncestors.length === 0) {
      return;
    }
    setExpandedIds((current) => {
      const next = new Set(current);
      selectedPathAncestors.forEach((id) => next.add(id));
      return next;
    });
  }, [selectedPathAncestors]);

  const saveDraft = useCallback(async () => {
    if (!node) {
      return;
    }

    const title = latestDraft.current.title.trim();
    const content = latestDraft.current.content;

    if (!title) {
      setSaveStatus('error');
      setLoadError('标题不能为空');
      return;
    }

    if (title === node.title && content === node.content) {
      setSaveStatus('saved');
      return;
    }

    try {
      setSaveStatus('saving');
      const updated = await lucasDbClient.updateNode(node.id, { title, content });
      setNode(updated);
      setDraftTitle(updated.title);
      setDraftContent(updated.content);
      latestDraft.current = { title: updated.title, content: updated.content };
      setSaveStatus('saved');
      await refreshTree();
    } catch (error) {
      setSaveStatus('error');
      setLoadError(error instanceof Error ? error.message : '保存失败');
    }
  }, [node, refreshTree]);

  const queueSave = useCallback(() => {
    setSaveStatus('idle');
    if (saveTimer.current) {
      window.clearTimeout(saveTimer.current);
    }
    saveTimer.current = window.setTimeout(() => {
      void saveDraft();
    }, 900);
  }, [saveDraft]);

  useEffect(() => {
    return () => {
      if (saveTimer.current) {
        window.clearTimeout(saveTimer.current);
      }
    };
  }, []);

  const handleTitleChange = (value: string) => {
    setDraftTitle(value);
    latestDraft.current.title = value;
    queueSave();
  };

  const handleContentChange = (value: string) => {
    setDraftContent(value);
    latestDraft.current.content = value;
    queueSave();
  };

  const handleCreateRoot = async () => {
    const created = await lucasDbClient.createNode({
      parent_id: null,
      title: '未命名节点'
    });
    await refreshTree();
    setSelectedNodeId(created.id);
  };

  const handleCreateChild = async () => {
    if (!selectedNodeId) {
      return;
    }
    const created = await lucasDbClient.createChildNode(selectedNodeId, {
      title: '未命名子节点'
    });
    await refreshTree();
    setExpandedIds((current) => new Set(current).add(selectedNodeId));
    setSelectedNodeId(created.id);
  };

  const handleCreateChildForNode = useCallback(
    async (nodeId: string) => {
      const created = await lucasDbClient.createChildNode(nodeId, {
        title: '未命名子节点'
      });
      await refreshTree();
      setExpandedIds((current) => new Set(current).add(nodeId));
      setSelectedNodeId(created.id);
    },
    [refreshTree]
  );

  const handleDelete = async () => {
    if (!selectedNodeId || !window.confirm('删除该节点及其子节点？')) {
      return;
    }

    await lucasDbClient.deleteNode(selectedNodeId);
    setSelectedNodeId(null);
    setNode(null);
    const nextTree = await refreshTree();
    const first = findFirstNode(nextTree);
    setSelectedNodeId(first?.id || null);
  };

  const handleUploadAttachments = async (files: File[]) => {
    if (!node || files.length === 0) {
      return;
    }

    try {
      setUploadingAttachments(true);
      setLoadError(null);
      for (const file of files) {
        await lucasDbClient.uploadAttachment(node.id, file);
      }
      await loadNode(node.id);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '上传附件失败');
    } finally {
      setUploadingAttachments(false);
    }
  };

  const handleDeleteAttachment = async (attachmentId: string) => {
    if (!node || !window.confirm('删除这个附件？')) {
      return;
    }

    try {
      await lucasDbClient.deleteAttachment(attachmentId);
      await loadNode(node.id);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '删除附件失败');
    }
  };

  const handleSearch = (query: string) => {
    setSearchQuery(query);
  };

  useEffect(() => {
    if (!searchQuery.trim()) {
      setSearchResults([]);
      setSearchLoading(false);
      return;
    }

    const timer = window.setTimeout(async () => {
      try {
        setSearchLoading(true);
        const results = await lucasDbClient.search(searchQuery);
        setSearchResults(results);
      } finally {
        setSearchLoading(false);
      }
    }, 250);

    return () => window.clearTimeout(timer);
  }, [searchQuery]);

  const handleSelectSearchResult = (id: string) => {
    setSelectedNodeId(id);
    setSearchQuery('');
    setSearchResults([]);
  };

  const handleViewModeChange = (mode: WorkspaceViewMode) => {
    setViewMode(mode);
  };

  const handleOpenSettings = async () => {
    setSettingsMessage(null);
    setShowToken(false);
    setSettingsOpen(true);
  };

  const copyText = async (text: string) => {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
  };

  const settingsExample = useMemo(() => {
    const token = apiTokenValue || '<YOUR_API_KEY>';
    return `curl -X GET "${apiTokenStatus?.apiBaseUrl || 'http://localhost:8765'}/api/tree" \\
  -H "Authorization: Bearer ${token}"

curl -X POST "${apiTokenStatus?.apiBaseUrl || 'http://localhost:8765'}/api/write-by-path" \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${token}" \\
  -d '{
    "path": "/项目A/开发日志/Agent测试",
    "content": "Agent 写入测试",
    "mode": "append",
    "actor": "agent"
  }'`;
  }, [apiTokenStatus?.apiBaseUrl, apiTokenValue]);

  const handleResetToken = async () => {
    try {
      setSettingsMessage(null);
      setSettingsBusy(true);
      const result = await lucasDbClient.resetApiToken();
      setApiTokenStatus((current) =>
        current
          ? {
              ...current,
              apiBaseUrl: result.apiBaseUrl,
              configured: true,
              source: 'database',
              tokenPreview: result.tokenPreview
            }
          : {
              ok: true,
              apiBaseUrl: result.apiBaseUrl,
              configured: true,
              source: 'database',
              tokenPreview: result.tokenPreview,
              copyable: false
            }
      );
      setApiTokenValue(result.token);
      localStorage.setItem('lucas_db_api_token', result.token);
      localStorage.setItem('lucas_db_api_token_source', 'database');
      setShowToken(true);
      const verification = await lucasDbClient.verifyApiToken(result.token);
      setSettingsMessage(
        verification.valid
          ? `已生成新的本地 API Key，验证通过：${verification.tokenPreview}`
          : '已生成新的本地 API Key，但验证未通过'
      );
    } catch (error) {
      setSettingsMessage(error instanceof Error ? error.message : '重置失败');
    } finally {
      setSettingsBusy(false);
    }
  };

  const handleVerifyToken = async () => {
    try {
      setSettingsMessage(null);
      setSettingsBusy(true);
      const result = await lucasDbClient.verifyApiToken(apiTokenValue || undefined);
      setSettingsMessage(
        result.valid
          ? `验证通过：${result.tokenPreview}（来源：${apiTokenSourceLabel[result.source]}）`
          : `验证失败：当前 Token 与服务端不匹配（服务端来源：${apiTokenSourceLabel[result.source]}）`
      );
    } catch (error) {
      setSettingsMessage(error instanceof Error ? error.message : '验证失败');
    } finally {
      setSettingsBusy(false);
    }
  };

  const handleCopyToken = async () => {
    if (!apiTokenValue) {
      return;
    }
    await copyText(apiTokenValue);
    setSettingsMessage('已复制 API Key');
  };

  const handleCopyExample = async () => {
    await copyText(settingsExample);
    setSettingsMessage('已复制调用示例');
  };

  return (
    <main className="workspace-page">
      <header className="app-header">
        <div className="brand">
          <img className="brand-mark" src={antTrailMark} alt="" />
          <div>
            <h1>AntTrail Database</h1>
            <span>蚁迹数据库</span>
          </div>
        </div>
        <div className="app-header-actions">
          <SearchBox
            query={searchQuery}
            results={searchResults}
            loading={searchLoading}
            onQueryChange={handleSearch}
            onSelect={handleSelectSearchResult}
            onClear={() => {
              setSearchQuery('');
              setSearchResults([]);
            }}
          />
          <a className="return-chat-button" href={CHAT_UI_URL}>
            <ArrowLeft size={16} />
            <span>返回对话</span>
          </a>
        </div>
      </header>

      <div className="workspace-shell">
        <aside className="sidebar">
          <div className="sidebar-title">
            <PanelLeftClose size={16} />
            <span>节点树</span>
          </div>
          <NodeTree
            nodes={tree}
            selectedNodeId={selectedNodeId}
            expandedIds={expandedIds}
            hoveredNodeId={hoveredNodeId}
            onToggle={(nodeId) => {
              setExpandedIds((current) => {
                const next = new Set(current);
                if (next.has(nodeId)) {
                  next.delete(nodeId);
                } else {
                  next.add(nodeId);
                }
                return next;
              });
            }}
            onSelect={setSelectedNodeId}
            onCreateChild={(nodeId) => void handleCreateChildForNode(nodeId).catch((error) => setLoadError(error.message))}
            onHover={setHoveredNodeId}
          />
          <button className="settings-button" type="button" onClick={() => void handleOpenSettings()}>
            <Settings size={16} />
            设置
          </button>
        </aside>

        <section className="editor-pane">
          <NodeToolbar
            selectedNodeId={selectedNodeId}
            saveStatus={saveStatus}
            viewMode={viewMode}
            onCreateRoot={() => void handleCreateRoot().catch((error) => setLoadError(error.message))}
            onCreateChild={() => void handleCreateChild().catch((error) => setLoadError(error.message))}
            onSave={() => void saveDraft()}
            onDelete={() => void handleDelete().catch((error) => setLoadError(error.message))}
            onRefresh={() => void refreshTree().catch((error) => setLoadError(error.message))}
            onViewModeChange={handleViewModeChange}
          />
          {loadError ? (
            <button className="error-banner" type="button" onClick={() => setLoadError(null)}>
              {loadError}
            </button>
          ) : null}
          {viewMode === 'edit' ? (
            <>
              <NodeCardPanel selectedNodeId={selectedNodeId} cards={nodeCards} />
              <ContentEditor
                disabled={!node}
                title={draftTitle}
                content={draftContent}
                attachments={node?.attachments || []}
                previewMarkdown={selectedMarkdownView}
                uploading={uploadingAttachments}
                onTitleChange={handleTitleChange}
                onContentChange={handleContentChange}
                onUploadAttachments={(files) => void handleUploadAttachments(files)}
                onDeleteAttachment={(attachmentId) => void handleDeleteAttachment(attachmentId)}
              />
            </>
          ) : (
            <GraphView selectedNodeId={selectedNodeId} />
          )}
        </section>
      </div>
      <SettingsDialog
        open={settingsOpen}
        status={apiTokenStatus}
        tokenValue={apiTokenValue}
        showToken={showToken}
        busy={settingsBusy}
        message={settingsMessage}
        exampleText={settingsExample}
        onClose={() => setSettingsOpen(false)}
        onToggleTokenVisibility={() => setShowToken((current) => !current)}
        onResetToken={() => void handleResetToken()}
        onVerifyToken={() => void handleVerifyToken()}
        onCopyToken={() => void handleCopyToken().catch((error) => setSettingsMessage(error.message))}
        onCopyExample={() => void handleCopyExample().catch((error) => setSettingsMessage(error.message))}
      />
    </main>
  );
}
