import { randomUUID } from 'node:crypto';
import { getDb } from '../db';

export interface EmbeddingJobRow {
  id: string;
  target_type: string;
  target_id: string;
  status: string;
  embedding_model: string;
  chunk_count: number | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

interface ListEmbeddingJobsInput {
  status?: unknown;
  target_type?: unknown;
  target_id?: unknown;
  limit?: unknown;
}

const asNonEmptyString = (value: unknown) =>
  typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;

const normalizeLimit = (value: unknown) => {
  const numeric = typeof value === 'string' ? Number(value) : value;
  if (typeof numeric !== 'number' || !Number.isFinite(numeric)) {
    return 50;
  }
  return Math.max(1, Math.min(200, Math.floor(numeric)));
};

export const createEmbeddingJob = (input: {
  targetType: string;
  targetId: string;
  embeddingModel: string;
}): EmbeddingJobRow => {
  const now = new Date().toISOString();
  const row: EmbeddingJobRow = {
    id: `emb_job_${randomUUID()}`,
    target_type: input.targetType,
    target_id: input.targetId,
    status: 'running',
    embedding_model: input.embeddingModel,
    chunk_count: 0,
    error: null,
    created_at: now,
    updated_at: now,
    completed_at: null
  };

  const db = getDb();
  db.prepare(
    `
      INSERT INTO embedding_jobs (
        id,
        target_type,
        target_id,
        status,
        embedding_model,
        chunk_count,
        error,
        created_at,
        updated_at,
        completed_at
      )
      VALUES (
        @id,
        @target_type,
        @target_id,
        @status,
        @embedding_model,
        @chunk_count,
        @error,
        @created_at,
        @updated_at,
        @completed_at
      )
    `
  ).run(row as unknown as Record<string, string | number | null>);

  return row;
};

export const completeEmbeddingJob = (id: string, chunkCount: number) => {
  const now = new Date().toISOString();
  const db = getDb();
  db.prepare(
    `
      UPDATE embedding_jobs
      SET status = 'completed',
          chunk_count = @chunk_count,
          error = NULL,
          updated_at = @now,
          completed_at = @now
      WHERE id = @id
    `
  ).run({
    id,
    chunk_count: chunkCount,
    now
  });
};

export const failEmbeddingJob = (id: string, error: string) => {
  const now = new Date().toISOString();
  const db = getDb();
  db.prepare(
    `
      UPDATE embedding_jobs
      SET status = 'failed',
          error = @error,
          updated_at = @now,
          completed_at = @now
      WHERE id = @id
    `
  ).run({
    id,
    error,
    now
  });
};

export const listEmbeddingJobs = (input: ListEmbeddingJobsInput = {}): EmbeddingJobRow[] => {
  const clauses: string[] = [];
  const args: Array<string | number> = [];
  const status = asNonEmptyString(input.status);
  const targetType = asNonEmptyString(input.target_type);
  const targetId = asNonEmptyString(input.target_id);

  if (status) {
    clauses.push('status = ?');
    args.push(status);
  }
  if (targetType) {
    clauses.push('target_type = ?');
    args.push(targetType);
  }
  if (targetId) {
    clauses.push('target_id = ?');
    args.push(targetId);
  }

  args.push(normalizeLimit(input.limit));
  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM embedding_jobs
        ${clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : ''}
        ORDER BY created_at DESC
        LIMIT ?
      `
    )
    .all(...args) as unknown as EmbeddingJobRow[];
};
