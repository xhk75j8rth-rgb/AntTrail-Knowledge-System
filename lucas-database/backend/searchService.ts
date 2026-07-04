import { getDb } from './db';
import type { NodeRow, SearchResult } from './types';

const escapeLike = (value: string) => value.replace(/[\\%_]/g, (match) => `\\${match}`);

const makeSnippet = (content: string, query: string) => {
  if (!content) {
    return '';
  }
  const lowerContent = content.toLowerCase();
  const lowerQuery = query.toLowerCase();
  const index = lowerContent.indexOf(lowerQuery);

  if (index === -1) {
    return content.slice(0, 120);
  }

  const start = Math.max(index - 40, 0);
  const end = Math.min(index + query.length + 80, content.length);
  return `${start > 0 ? '...' : ''}${content.slice(start, end)}${end < content.length ? '...' : ''}`;
};

const buildPathMap = (rows: Pick<NodeRow, 'id' | 'parent_id' | 'title'>[]) => {
  const byId = new Map(rows.map((row) => [row.id, row]));
  const cache = new Map<string, string>();

  const getPath = (id: string): string => {
    const cached = cache.get(id);
    if (cached) {
      return cached;
    }

    const row = byId.get(id);
    if (!row) {
      return '/';
    }

    const path = row.parent_id ? `${getPath(row.parent_id)}/${row.title}` : `/${row.title}`;
    cache.set(id, path);
    return path;
  };

  return getPath;
};

export const searchNodes = (query: unknown): SearchResult[] => {
  if (typeof query !== 'string' || query.trim().length === 0) {
    return [];
  }

  const q = query.trim();
  const db = getDb();
  const allNodes = db
    .prepare('SELECT id, parent_id, title FROM nodes WHERE deleted_at IS NULL')
    .all() as unknown as Pick<NodeRow, 'id' | 'parent_id' | 'title'>[];
  const getPath = buildPathMap(allNodes);

  const rows = db
    .prepare(
      `
        SELECT *
        FROM nodes
        WHERE deleted_at IS NULL
          AND (title LIKE @like ESCAPE '\\' OR content LIKE @like ESCAPE '\\')
        ORDER BY updated_at DESC
        LIMIT 50
      `
    )
    .all({ like: `%${escapeLike(q)}%` }) as unknown as NodeRow[];

  return rows.map((row) => ({
    id: row.id,
    title: row.title,
    path: getPath(row.id),
    snippet: row.title.includes(q) ? row.content.slice(0, 120) : makeSnippet(row.content, q)
  }));
};
