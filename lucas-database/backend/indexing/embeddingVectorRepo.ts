import { ApiError } from '../errors';
import { getDb } from '../db';

export interface EmbeddingVectorRow {
  id: string;
  chunk_id: string;
  vector_store: string;
  vector_id: string;
  embedding_model: string;
  embedding_dim: number;
  text_hash: string;
  metadata_json: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

interface UpsertEmbeddingVectorInput {
  id: string;
  chunkId: string;
  vectorStore: string;
  vectorId: string;
  embeddingModel: string;
  embeddingDim: number;
  textHash: string;
  metadata?: Record<string, unknown>;
}

export const softDeleteVectorsForTarget = (
  targetType: string,
  targetId: string,
  deletedAt: string
) => {
  const db = getDb();
  db.prepare(
    `
      UPDATE embedding_vectors
      SET deleted_at = @deleted_at,
          updated_at = @deleted_at
      WHERE deleted_at IS NULL
        AND chunk_id IN (
          SELECT id
          FROM chunks
          WHERE target_type = @target_type
            AND target_id = @target_id
        )
    `
  ).run({
    target_type: targetType,
    target_id: targetId,
    deleted_at: deletedAt
  });
};

export const upsertEmbeddingVector = (
  input: UpsertEmbeddingVectorInput,
  now = new Date().toISOString()
) => {
  const db = getDb();
  const row = {
    id: input.id,
    chunk_id: input.chunkId,
    vector_store: input.vectorStore,
    vector_id: input.vectorId,
    embedding_model: input.embeddingModel,
    embedding_dim: input.embeddingDim,
    text_hash: input.textHash,
    metadata_json: input.metadata ? JSON.stringify(input.metadata) : null,
    created_at: now,
    updated_at: now
  };

  db.prepare(
    `
      INSERT INTO embedding_vectors (
        id,
        chunk_id,
        vector_store,
        vector_id,
        embedding_model,
        embedding_dim,
        text_hash,
        metadata_json,
        created_at,
        updated_at,
        deleted_at
      )
      VALUES (
        @id,
        @chunk_id,
        @vector_store,
        @vector_id,
        @embedding_model,
        @embedding_dim,
        @text_hash,
        @metadata_json,
        @created_at,
        @updated_at,
        NULL
      )
      ON CONFLICT(id) DO UPDATE SET
        vector_store = excluded.vector_store,
        vector_id = excluded.vector_id,
        embedding_model = excluded.embedding_model,
        embedding_dim = excluded.embedding_dim,
        text_hash = excluded.text_hash,
        metadata_json = excluded.metadata_json,
        updated_at = excluded.updated_at,
        deleted_at = NULL
    `
  ).run(row);
};

export const getActiveVectorByChunkId = (chunkId: string): EmbeddingVectorRow => {
  const db = getDb();
  const row = db
    .prepare(
      `
        SELECT *
        FROM embedding_vectors
        WHERE chunk_id = ?
          AND deleted_at IS NULL
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
      `
    )
    .get(chunkId) as EmbeddingVectorRow | undefined;

  if (!row) {
    throw new ApiError('VECTOR_NOT_FOUND', 'Embedding vector not found', 404);
  }

  return row;
};

export const listActiveVectorsByStore = (input: {
  vectorStore: string;
  embeddingModel: string;
  embeddingDim: number;
  limit?: number;
}): EmbeddingVectorRow[] => {
  const db = getDb();
  const limit = Math.max(1, Math.min(input.limit ?? 5000, 50000));
  return db
    .prepare(
      `
        SELECT *
        FROM embedding_vectors
        WHERE vector_store = @vector_store
          AND embedding_model = @embedding_model
          AND embedding_dim = @embedding_dim
          AND deleted_at IS NULL
        ORDER BY updated_at DESC, created_at DESC
        LIMIT @limit
      `
    )
    .all({
      vector_store: input.vectorStore,
      embedding_model: input.embeddingModel,
      embedding_dim: input.embeddingDim,
      limit
    }) as unknown as EmbeddingVectorRow[];
};
