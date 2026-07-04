import { ApiError } from './errors';
import { appendToNode, createNode, findChildByTitle, updateNode } from './nodesRepo';
import type { NodeRow } from './types';

export interface WriteByPathInput {
  path: string;
  content: string;
  mode: 'overwrite' | 'append';
  actor?: string;
}

const normalizePath = (path: string) =>
  path
    .split('/')
    .map((segment) => segment.trim())
    .filter(Boolean);

export const writeByPath = (input: WriteByPathInput) => {
  if (typeof input.path !== 'string' || input.path.trim().length === 0) {
    throw new ApiError('PATH_REQUIRED', 'path is required', 400);
  }
  if (input.mode !== 'overwrite' && input.mode !== 'append') {
    throw new ApiError('INVALID_MODE', 'mode must be append or overwrite', 400);
  }
  if (typeof input.content !== 'string') {
    throw new ApiError('CONTENT_REQUIRED', 'content is required', 400);
  }

  const segments = normalizePath(input.path);
  if (segments.length === 0) {
    throw new ApiError('PATH_REQUIRED', 'path is required', 400);
  }

  let parentId: string | null = null;
  let current: NodeRow | undefined;
  let createdFinalNode = false;

  segments.forEach((segment, index) => {
    const existing = findChildByTitle(parentId, segment);
    if (existing) {
      current = existing;
      parentId = existing.id;
      return;
    }

    current = createNode({
      parent_id: parentId,
      title: segment,
      content: '',
      actor: input.actor || 'agent'
    });
    createdFinalNode = index === segments.length - 1;
    parentId = current.id;
  });

  if (!current) {
    throw new ApiError('PATH_REQUIRED', 'path is required', 400);
  }

  const updated =
    input.mode === 'append'
      ? appendToNode(current.id, input.content, input.actor || 'agent')
      : updateNode(current.id, { content: input.content, actor: input.actor || 'agent' });

  return {
    id: updated.id,
    path: `/${segments.join('/')}`,
    title: updated.title,
    mode: input.mode,
    created: createdFinalNode,
    updated_at: updated.updated_at
  };
};
