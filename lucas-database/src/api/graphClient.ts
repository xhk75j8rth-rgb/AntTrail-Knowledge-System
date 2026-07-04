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

export type GraphScope = 'local' | 'global';

export interface CreateRelationInput {
  from_node_id?: string;
  to_node_id?: string;
  from_card_id?: string;
  to_card_id?: string;
  relation_type: string;
  label?: string;
  source?: string;
  confidence?: number;
  metadata?: Record<string, unknown>;
}

export interface RelationRecord {
  id: string;
  from_node_id: string | null;
  to_node_id: string | null;
  from_card_id: string | null;
  to_card_id: string | null;
  relation_type: string;
  label: string | null;
  source: string;
  confidence: number;
  metadata_json: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
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
  if (options.body && !(options.body instanceof FormData)) {
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

export const graphClient = {
  getGraph: (input: { nodeId?: string | null; depth?: number; scope?: GraphScope } = {}) => {
    const params = new URLSearchParams();
    if (input.nodeId && input.scope !== 'global') {
      params.set('node_id', input.nodeId);
    }
    params.set('depth', String(input.depth ?? 1));
    params.set('scope', input.scope || 'local');
    return request<GraphData>(`/api/graph?${params.toString()}`);
  },
  createRelation: (input: CreateRelationInput) =>
    request<RelationRecord>('/api/relations', {
      method: 'POST',
      body: JSON.stringify({
        ...input,
        source: input.source || 'manual'
      })
    })
};
