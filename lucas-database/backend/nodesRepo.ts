import { randomUUID } from 'node:crypto';
import { ApiError } from './errors';
import { getDb, withTransaction } from './db';
import { recordNodeEvent } from './nodeEventsRepo';
import type { NodeAttachment, NodeDetail, NodeRow, TreeNode } from './types';

export interface CreateNodeInput {
  parent_id?: string | null;
  title: string;
  content?: string;
  type?: string;
  actor?: string;
}

export interface UpdateNodeInput {
  title?: string;
  content?: string;
  actor?: string;
}

const slugify = (title: string) =>
  title
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^\p{L}\p{N}-]/gu, '')
    .slice(0, 120);

export const validateTitle = (title: unknown) => {
  if (typeof title !== 'string' || title.trim().length === 0) {
    throw new ApiError('TITLE_REQUIRED', 'title is required', 400);
  }
};

export const getNodeById = (id: string, includeDeleted = false): NodeRow | undefined => {
  const db = getDb();
  const sql = includeDeleted
    ? 'SELECT * FROM nodes WHERE id = ?'
    : 'SELECT * FROM nodes WHERE id = ? AND deleted_at IS NULL';
  return db.prepare(sql).get(id) as NodeRow | undefined;
};

export const getNodeOrThrow = (id: string): NodeRow => {
  const node = getNodeById(id);
  if (!node) {
    throw new ApiError('NODE_NOT_FOUND', 'Node not found', 404);
  }
  return node;
};

export const listActiveNodes = (): NodeRow[] => {
  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM nodes
        WHERE deleted_at IS NULL
        ORDER BY parent_id IS NOT NULL, parent_id, sort_order, created_at
      `
    )
    .all() as unknown as NodeRow[];
};

export const buildTree = (rows = listActiveNodes()): TreeNode[] => {
  const nodeMap = new Map<string, TreeNode>();
  const roots: TreeNode[] = [];

  rows.forEach((row) => {
    nodeMap.set(row.id, {
      id: row.id,
      parent_id: row.parent_id,
      title: row.title,
      type: row.type,
      children: []
    });
  });

  rows.forEach((row) => {
    const current = nodeMap.get(row.id);
    if (!current) {
      return;
    }

    if (row.parent_id && nodeMap.has(row.parent_id)) {
      nodeMap.get(row.parent_id)?.children.push(current);
    } else {
      roots.push(current);
    }
  });

  return roots;
};

export const getChildren = (parentId: string) => {
  const db = getDb();
  return db
    .prepare(
      `
        SELECT id, parent_id, title, type
        FROM nodes
        WHERE parent_id = ? AND deleted_at IS NULL
        ORDER BY sort_order, created_at
      `
    )
    .all(parentId) as unknown as NodeDetail['children'];
};

export const getNodeAttachments = (nodeId: string): NodeAttachment[] => {
  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM node_attachments
        WHERE node_id = ?
          AND deleted_at IS NULL
        ORDER BY created_at DESC
      `
    )
    .all(nodeId) as unknown as NodeAttachment[];
};

export const getNodeDetail = (id: string): NodeDetail => {
  const node = getNodeOrThrow(id);
  return {
    ...node,
    children: getChildren(id),
    attachments: getNodeAttachments(id)
  };
};

export const getNextSortOrder = (parentId: string | null) => {
  const db = getDb();
  const row = db
    .prepare(
      `
        SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_sort_order
        FROM nodes
        WHERE (
          (parent_id IS NULL AND @parent_id IS NULL)
          OR parent_id = @parent_id
        )
      `
    )
    .get({ parent_id: parentId }) as { next_sort_order: number };
  return row.next_sort_order;
};

export const createNode = (input: CreateNodeInput): NodeRow => {
  validateTitle(input.title);
  const parentId = input.parent_id ?? null;

  if (parentId) {
    getNodeOrThrow(parentId);
  }

  const now = new Date().toISOString();
  const node: NodeRow = {
    id: `node_${randomUUID()}`,
    parent_id: parentId,
    title: input.title.trim(),
    slug: slugify(input.title),
    type: input.type || 'page',
    content: input.content || '',
    sort_order: getNextSortOrder(parentId),
    created_at: now,
    updated_at: now,
    deleted_at: null
  };

  const db = getDb();
  withTransaction(() => {
    db.prepare(
      `
        INSERT INTO nodes (
          id,
          parent_id,
          title,
          slug,
          type,
          content,
          sort_order,
          created_at,
          updated_at,
          deleted_at
        )
        VALUES (
          @id,
          @parent_id,
          @title,
          @slug,
          @type,
          @content,
          @sort_order,
          @created_at,
          @updated_at,
          @deleted_at
        )
      `
    ).run(node as unknown as Record<string, string | number | null>);

    recordNodeEvent({
      nodeId: node.id,
      actor: input.actor,
      action: 'create',
      afterSnapshot: node
    });
  });
  return node;
};

export const createChildNode = (
  parentId: string,
  input: Omit<CreateNodeInput, 'parent_id'>
): NodeRow => {
  return createNode({
    parent_id: parentId,
    ...input
  });
};

export const updateNode = (id: string, input: UpdateNodeInput): NodeRow => {
  const before = getNodeOrThrow(id);

  if (input.title !== undefined) {
    validateTitle(input.title);
  }

  if (input.title === undefined && input.content === undefined) {
    throw new ApiError('NO_FIELDS_TO_UPDATE', 'title or content is required', 400);
  }

  const after: NodeRow = {
    ...before,
    title: input.title === undefined ? before.title : input.title.trim(),
    slug: input.title === undefined ? before.slug : slugify(input.title),
    content: input.content === undefined ? before.content : input.content,
    updated_at: new Date().toISOString()
  };

  const db = getDb();
  withTransaction(() => {
    db.prepare(
      `
        UPDATE nodes
        SET title = @title,
            slug = @slug,
            content = @content,
            updated_at = @updated_at
        WHERE id = @id AND deleted_at IS NULL
      `
    ).run({
      id: after.id,
      title: after.title,
      slug: after.slug,
      content: after.content,
      updated_at: after.updated_at
    });

    recordNodeEvent({
      nodeId: id,
      actor: input.actor,
      action: 'update',
      beforeSnapshot: before,
      afterSnapshot: after
    });
  });
  return after;
};

export const appendToNode = (id: string, content: unknown, actor?: string): NodeRow => {
  if (typeof content !== 'string' || content.length === 0) {
    throw new ApiError('CONTENT_REQUIRED', 'content is required', 400);
  }

  const before = getNodeOrThrow(id);
  const after: NodeRow = {
    ...before,
    content: `${before.content}${content}`,
    updated_at: new Date().toISOString()
  };

  const db = getDb();
  withTransaction(() => {
    db.prepare(
      `
        UPDATE nodes
        SET content = @content,
            updated_at = @updated_at
        WHERE id = @id AND deleted_at IS NULL
      `
    ).run({
      id: after.id,
      content: after.content,
      updated_at: after.updated_at
    });

    recordNodeEvent({
      nodeId: id,
      actor,
      action: 'append',
      beforeSnapshot: before,
      afterSnapshot: after
    });
  });
  return after;
};

const getActiveDescendantIds = (rootId: string): string[] => {
  const db = getDb();
  const rows = db
    .prepare('SELECT id, parent_id FROM nodes WHERE deleted_at IS NULL')
    .all() as unknown as Pick<NodeRow, 'id' | 'parent_id'>[];
  const childrenByParent = new Map<string, string[]>();

  rows.forEach((row) => {
    if (!row.parent_id) {
      return;
    }
    const siblings = childrenByParent.get(row.parent_id) || [];
    siblings.push(row.id);
    childrenByParent.set(row.parent_id, siblings);
  });

  const ids: string[] = [];
  const walk = (id: string) => {
    ids.push(id);
    (childrenByParent.get(id) || []).forEach(walk);
  };

  walk(rootId);
  return ids;
};

export const softDeleteNode = (id: string, actor?: string) => {
  const before = getNodeOrThrow(id);
  const ids = getActiveDescendantIds(id);
  const deletedAt = new Date().toISOString();
  const placeholders = ids.map(() => '?').join(', ');
  const db = getDb();

  withTransaction(() => {
    db.prepare(
      `
        UPDATE nodes
        SET deleted_at = ?,
            updated_at = ?
        WHERE id IN (${placeholders}) AND deleted_at IS NULL
      `
    ).run(deletedAt, deletedAt, ...ids);

    recordNodeEvent({
      nodeId: id,
      actor,
      action: 'delete',
      beforeSnapshot: before,
      afterSnapshot: {
        deleted_at: deletedAt,
        deleted_ids: ids
      }
    });
  });
  return { deleted_at: deletedAt, deleted_ids: ids };
};

export const findChildByTitle = (
  parentId: string | null,
  title: string
): NodeRow | undefined => {
  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM nodes
        WHERE deleted_at IS NULL
          AND title = @title
          AND (
            (parent_id IS NULL AND @parent_id IS NULL)
            OR parent_id = @parent_id
          )
        ORDER BY sort_order, created_at
        LIMIT 1
      `
    )
    .get({ parent_id: parentId, title }) as NodeRow | undefined;
};
