import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { ATTACHMENTS_DIR, getDb, withTransaction } from './db';
import { ApiError } from './errors';
import { recordNodeEvent } from './nodeEventsRepo';
import { getNodeOrThrow } from './nodesRepo';
import type { NodeAttachment, NodeAttachmentKind } from './types';

export interface CreateAttachmentInput {
  nodeId: string;
  originalName: string;
  mimeType?: string;
  sizeBytes: number;
  tempPath: string;
  actor?: string;
}

export interface CopyLocalAttachmentInput {
  nodeId: string;
  filePath: string;
  originalName?: string;
  mimeType?: string;
  actor?: string;
}

export interface AttachmentPersistOptions {
  transaction?: boolean;
}

type PersistMode = 'move' | 'copy';

const mimeByExtension: Record<string, string> = {
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.bmp': 'image/bmp',
  '.svg': 'image/svg+xml',
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.mov': 'video/quicktime',
  '.m4v': 'video/mp4',
  '.pdf': 'application/pdf',
  '.txt': 'text/plain',
  '.md': 'text/markdown'
};

const sanitizeFileName = (name: string) => {
  const trimmed = path.basename(name || 'attachment').trim();
  const safe = trimmed.replace(/[<>:"/\\|?*\x00-\x1F]/g, '_').replace(/\s+/g, ' ');
  return safe.slice(0, 160) || 'attachment';
};

const inferMimeType = (filePath: string, mimeType?: string) =>
  mimeType || mimeByExtension[path.extname(filePath).toLowerCase()] || 'application/octet-stream';

const getAttachmentKind = (mimeType: string): NodeAttachmentKind => {
  if (mimeType.startsWith('image/')) {
    return 'image';
  }

  if (mimeType.startsWith('video/')) {
    return 'video';
  }

  return 'file';
};

const getRelativePath = (nodeId: string, storedName: string) =>
  path.join(nodeId, storedName).replace(/\\/g, '/');

const resolveAttachmentPath = (relativePath: string) => {
  const absolutePath = path.resolve(ATTACHMENTS_DIR, relativePath);
  const root = path.resolve(ATTACHMENTS_DIR);

  if (absolutePath !== root && !absolutePath.startsWith(`${root}${path.sep}`)) {
    throw new ApiError('ATTACHMENT_PATH_INVALID', 'Attachment path is invalid', 400);
  }

  return absolutePath;
};

export const getAttachmentUrl = (attachmentId: string) =>
  `/api/attachments/${encodeURIComponent(attachmentId)}/file`;

export const getAssetUrl = (attachmentId: string) =>
  `/api/assets/${encodeURIComponent(attachmentId)}`;

export const listNodeAttachments = (nodeId: string): NodeAttachment[] => {
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

export const getAttachmentOrThrow = (attachmentId: string): NodeAttachment => {
  const db = getDb();
  const attachment = db
    .prepare(
      `
        SELECT *
        FROM node_attachments
        WHERE id = ?
          AND deleted_at IS NULL
      `
    )
    .get(attachmentId) as NodeAttachment | undefined;

  if (!attachment) {
    throw new ApiError('ATTACHMENT_NOT_FOUND', 'Attachment not found', 404);
  }

  return attachment;
};

export const getAttachmentFilePath = (attachment: NodeAttachment) =>
  resolveAttachmentPath(attachment.relative_path);

const persistAttachment = (input: {
  nodeId: string;
  originalName: string;
  mimeType?: string;
  sizeBytes: number;
  sourcePath: string;
  actor?: string;
  mode: PersistMode;
  transaction?: boolean;
}): NodeAttachment => {
  const node = getNodeOrThrow(input.nodeId);
  const mimeType = inferMimeType(input.originalName, input.mimeType);
  const safeOriginalName = sanitizeFileName(input.originalName);
  const id = `att_${randomUUID()}`;
  const storedName = `${id}_${safeOriginalName}`;
  const relativePath = getRelativePath(input.nodeId, storedName);
  const targetPath = resolveAttachmentPath(relativePath);
  const now = new Date().toISOString();
  const attachment: NodeAttachment = {
    id,
    node_id: input.nodeId,
    original_name: safeOriginalName,
    stored_name: storedName,
    relative_path: relativePath,
    mime_type: mimeType,
    size_bytes: input.sizeBytes,
    kind: getAttachmentKind(mimeType),
    created_at: now,
    deleted_at: null
  };

  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  if (input.mode === 'copy') {
    fs.copyFileSync(input.sourcePath, targetPath);
  } else {
    fs.renameSync(input.sourcePath, targetPath);
  }

  const db = getDb();
  const insertRecord = () => {
    db.prepare(
      `
        INSERT INTO node_attachments (
          id,
          node_id,
          original_name,
          stored_name,
          relative_path,
          mime_type,
          size_bytes,
          kind,
          created_at,
          deleted_at
        )
        VALUES (
          @id,
          @node_id,
          @original_name,
          @stored_name,
          @relative_path,
          @mime_type,
          @size_bytes,
          @kind,
          @created_at,
          @deleted_at
        )
      `
    ).run(attachment as unknown as Record<string, string | number | null>);

    recordNodeEvent({
      nodeId: input.nodeId,
      actor: input.actor,
      action: 'attach',
      beforeSnapshot: {
        attachment_count: listNodeAttachments(input.nodeId).length - 1
      },
      afterSnapshot: {
        node_id: node.id,
        attachment
      }
    });
  };

  try {
    if (input.transaction === false) {
      insertRecord();
    } else {
      withTransaction(insertRecord);
    }
  } catch (error) {
    fs.rmSync(targetPath, { force: true });
    throw error;
  }

  return attachment;
};

export const createAttachment = (input: CreateAttachmentInput): NodeAttachment =>
  persistAttachment({
    nodeId: input.nodeId,
    originalName: input.originalName,
    mimeType: input.mimeType,
    sizeBytes: input.sizeBytes,
    sourcePath: input.tempPath,
    actor: input.actor,
    mode: 'move'
  });

export const copyLocalFileToAttachment = (
  input: CopyLocalAttachmentInput,
  options: AttachmentPersistOptions = {}
): NodeAttachment => {
  const absolutePath = path.resolve(input.filePath);
  const stat = fs.statSync(absolutePath);
  if (!stat.isFile()) {
    throw new ApiError('ATTACHMENT_SOURCE_INVALID', 'Attachment source is not a file', 400);
  }

  return persistAttachment({
    nodeId: input.nodeId,
    originalName: input.originalName || path.basename(absolutePath),
    mimeType: inferMimeType(absolutePath, input.mimeType),
    sizeBytes: stat.size,
    sourcePath: absolutePath,
    actor: input.actor,
    mode: 'copy',
    transaction: options.transaction
  });
};

export const softDeleteAttachment = (attachmentId: string, actor?: string) => {
  const before = getAttachmentOrThrow(attachmentId);
  const deletedAt = new Date().toISOString();
  const db = getDb();

  withTransaction(() => {
    db.prepare(
      `
        UPDATE node_attachments
        SET deleted_at = ?
        WHERE id = ?
          AND deleted_at IS NULL
      `
    ).run(deletedAt, attachmentId);

    recordNodeEvent({
      nodeId: before.node_id,
      actor,
      action: 'detach',
      beforeSnapshot: before,
      afterSnapshot: {
        deleted_at: deletedAt
      }
    });
  });

  return { ok: true, deleted_at: deletedAt };
};
