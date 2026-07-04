export interface TreeNode {
  id: string;
  parent_id: string | null;
  title: string;
  type: string;
  children: TreeNode[];
}

export interface NodeDetail {
  id: string;
  parent_id: string | null;
  title: string;
  type: string;
  content: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  children: Array<{
    id: string;
    parent_id: string | null;
    title: string;
    type: string;
  }>;
  attachments: NodeAttachment[];
}

export type NodeAttachmentKind = 'image' | 'video' | 'file';

export interface NodeAttachment {
  id: string;
  node_id: string;
  original_name: string;
  mime_type: string;
  size_bytes: number;
  kind: NodeAttachmentKind;
  created_at: string;
  deleted_at: string | null;
  url: string;
}

export interface SearchResult {
  id: string;
  title: string;
  path: string;
  snippet: string;
}

export interface ApiTokenStatus {
  ok: true;
  apiBaseUrl: string;
  configured: boolean;
  source: 'env' | 'local_config' | 'database';
  tokenPreview: string | null;
  copyable: boolean;
}

export interface ResetApiTokenResult {
  ok: true;
  apiBaseUrl: string;
  token: string;
  tokenPreview: string;
}

export interface VerifyApiTokenResult {
  ok: true;
  valid: boolean;
  tokenPreview: string | null;
  source: ApiTokenStatus['source'];
}

export interface NodeCardSummary {
  id: string;
  node_id: string;
  schema_name: string;
  schema_version: string;
  card_type: string | null;
  quality_level: string | null;
  source_title: string | null;
  display_title: string;
  one_sentence_summary: string | null;
  markdown_view: string | null;
  plain_text_view: string | null;
  created_at: string;
  updated_at: string;
  tags: string[];
}

export interface GraphNode {
  id: string;
  entity_type: string;
  label: string;
  source_id?: string;
  metadata?: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  from: string;
  to: string;
  relation_type: string;
  label: string | null;
  source: string;
  confidence: number;
  metadata?: Record<string, unknown>;
}

export interface GraphData {
  ok: true;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

const getBaseUrl = () =>
  window.lucasDbConfig?.apiBaseUrl ||
  import.meta.env.VITE_LUCAS_DB_API_BASE_URL ||
  'http://localhost:8765';

const getToken = () => {
  const envToken =
    window.lucasDbConfig?.apiToken ||
    import.meta.env.VITE_LUCAS_DB_API_TOKEN ||
    'lucas-local-dev-token';

  if (
    window.lucasDbConfig?.apiTokenSource === 'env' ||
    localStorage.getItem('lucas_db_api_token_source') === 'env'
  ) {
    return envToken;
  }

  return localStorage.getItem('lucas_db_api_token') || envToken;
};

const request = async <T>(path: string, options: RequestInit = {}): Promise<T> => {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }

  const method = (options.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD' && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${getToken()}`);
  }

  const response = await fetch(`${getBaseUrl()}${path}`, {
    ...options,
    headers
  });

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message = data?.error?.message || `Request failed: ${response.status}`;
    throw new Error(message);
  }

  return data as T;
};

export const toAbsoluteApiUrl = (path: string) => {
  if (/^https?:\/\//i.test(path)) {
    return path;
  }

  return `${getBaseUrl()}${path}`;
};

export const lucasDbClient = {
  getTree: () => request<TreeNode[]>('/api/tree'),
  getNode: (id: string) => request<NodeDetail>(`/api/nodes/${id}`),
  createNode: (input: { parent_id: string | null; title: string; content?: string }) =>
    request<NodeDetail>('/api/nodes', {
      method: 'POST',
      body: JSON.stringify({
        ...input,
        actor: 'user'
      })
    }),
  updateNode: (id: string, input: { title?: string; content?: string }) =>
    request<NodeDetail>(`/api/nodes/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        ...input,
        actor: 'user'
      })
    }),
  uploadAttachment: (id: string, file: File) => {
    const formData = new FormData();
    formData.set('file', file);
    formData.set('actor', 'user');
    return request<{ ok: true; attachment: NodeAttachment }>(`/api/nodes/${id}/attachments`, {
      method: 'POST',
      body: formData
    });
  },
  deleteAttachment: (id: string) =>
    request<{ ok: true; deleted_at: string }>(`/api/attachments/${id}`, {
      method: 'DELETE',
      body: JSON.stringify({ actor: 'user' })
    }),
  getAttachmentUrl: (attachment: Pick<NodeAttachment, 'url'>) => toAbsoluteApiUrl(attachment.url),
  deleteNode: (id: string) =>
    request<{ ok: boolean; deleted_at: string; deleted_ids: string[] }>(`/api/nodes/${id}`, {
      method: 'DELETE',
      body: JSON.stringify({ actor: 'user' })
    }),
  createChildNode: (
    id: string,
    input: { title: string; content?: string; type?: string }
  ) =>
    request<NodeDetail>(`/api/nodes/${id}/children`, {
      method: 'POST',
      body: JSON.stringify({
        ...input,
        actor: 'user'
      })
    }),
  search: (query: string) =>
    request<SearchResult[]>(`/api/search?q=${encodeURIComponent(query)}`),
  getNodeCards: (id: string) =>
    request<{ ok: true; node_id: string; cards: NodeCardSummary[] }>(
      `/api/nodes/${id}/cards`
    ),
  getGraph: (nodeId: string, depth = 1) =>
    request<GraphData>(`/api/graph?node_id=${encodeURIComponent(nodeId)}&depth=${depth}`),
  getApiTokenStatus: () => request<ApiTokenStatus>('/api/settings/api-token'),
  resetApiToken: () =>
    request<ResetApiTokenResult>('/api/settings/api-token/reset', {
      method: 'POST',
      body: JSON.stringify({})
    }),
  verifyApiToken: (token?: string) =>
    request<VerifyApiTokenResult>('/api/settings/api-token/verify', {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      body: JSON.stringify({})
    })
};
