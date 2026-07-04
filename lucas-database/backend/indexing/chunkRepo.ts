import { ApiError } from '../errors';
import { getDb } from '../db';
import type { ChunkOutput, ChunkRow } from './chunkTypes';

const metadataToJson = (metadata: Record<string, unknown> | undefined) =>
  metadata ? JSON.stringify(metadata) : null;

export const softDeleteChunksForTarget = (
  targetType: string,
  targetId: string,
  deletedAt: string
) => {
  const db = getDb();
  db.prepare(
    `
      UPDATE chunks
      SET deleted_at = @deleted_at,
          updated_at = @deleted_at
      WHERE target_type = @target_type
        AND target_id = @target_id
        AND deleted_at IS NULL
    `
  ).run({
    target_type: targetType,
    target_id: targetId,
    deleted_at: deletedAt
  });
};

export const upsertChunks = (chunks: ChunkOutput[], now = new Date().toISOString()) => {
  const db = getDb();
  const statement = db.prepare(
    `
      INSERT INTO chunks (
        id,
        target_type,
        target_id,
        source_table,
        chunk_type,
        chunk_text,
        chunk_index,
        text_hash,
        token_count,
        metadata_json,
        created_at,
        updated_at,
        deleted_at
      )
      VALUES (
        @id,
        @target_type,
        @target_id,
        @source_table,
        @chunk_type,
        @chunk_text,
        @chunk_index,
        @text_hash,
        @token_count,
        @metadata_json,
        @created_at,
        @updated_at,
        NULL
      )
      ON CONFLICT(id) DO UPDATE SET
        source_table = excluded.source_table,
        chunk_type = excluded.chunk_type,
        chunk_text = excluded.chunk_text,
        chunk_index = excluded.chunk_index,
        text_hash = excluded.text_hash,
        token_count = excluded.token_count,
        metadata_json = excluded.metadata_json,
        updated_at = excluded.updated_at,
        deleted_at = NULL
    `
  );

  chunks.forEach((chunk) => {
    if (!chunk.id) {
      throw new ApiError('CHUNK_ID_REQUIRED', 'chunk id is required', 500);
    }

    statement.run({
      id: chunk.id,
      target_type: chunk.targetType,
      target_id: chunk.targetId,
      source_table: chunk.sourceTable || null,
      chunk_type: chunk.chunkType,
      chunk_text: chunk.chunkText,
      chunk_index: chunk.chunkIndex,
      text_hash: chunk.textHash,
      token_count: chunk.tokenCount ?? 0,
      metadata_json: metadataToJson(chunk.metadata),
      created_at: now,
      updated_at: now
    });
  });
};

export const listChunksByTarget = (targetType: string, targetId: string): ChunkRow[] => {
  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM chunks
        WHERE target_type = ?
          AND target_id = ?
          AND deleted_at IS NULL
        ORDER BY chunk_index, created_at
      `
    )
    .all(targetType, targetId) as unknown as ChunkRow[];
};

export const getChunkOrThrow = (id: string): ChunkRow => {
  const db = getDb();
  const chunk = db
    .prepare('SELECT * FROM chunks WHERE id = ? AND deleted_at IS NULL')
    .get(id) as ChunkRow | undefined;
  if (!chunk) {
    throw new ApiError('CHUNK_NOT_FOUND', 'Chunk not found', 404);
  }
  return chunk;
};
