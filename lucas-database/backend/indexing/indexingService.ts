import { ApiError } from '../errors';
import { getDb, withTransaction } from '../db';
import { getCardOrThrow } from '../cardsRepo';
import { getNodeOrThrow } from '../nodesRepo';
import { CardAwareChunker } from './cardAwareChunker';
import { softDeleteChunksForTarget, upsertChunks } from './chunkRepo';
import type { ChunkOutput } from './chunkTypes';
import {
  completeEmbeddingJob,
  createEmbeddingJob,
  failEmbeddingJob
} from './embeddingJobRepo';
import { softDeleteVectorsForTarget } from './embeddingVectorRepo';
import { MarkdownChunker } from './markdownChunker';
import { embeddingProvider, vectorStore } from './indexingRuntime';

export interface RebuildIndexResult {
  ok: true;
  job_id: string;
  target_type: string;
  target_id: string;
  chunk_count: number;
  embedding_model: string;
}

export interface RebuildAllIndexesInput {
  targetTypes?: unknown;
  limit?: unknown;
}

export interface RebuildAllIndexesResult {
  ok: true;
  rebuilt: {
    nodes: number;
    cards: number;
  };
  target_count: number;
  chunk_count: number;
  embedding_model: string;
  vector_store: string;
  errors: Array<{
    target_type: string;
    target_id: string;
    message: string;
  }>;
}

const markdownChunker = new MarkdownChunker();
const cardAwareChunker = new CardAwareChunker();

const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

const replaceChunksForTarget = (targetType: string, targetId: string, chunks: ChunkOutput[]) => {
  const deletedAt = new Date().toISOString();
  withTransaction(() => {
    softDeleteVectorsForTarget(targetType, targetId, deletedAt);
    softDeleteChunksForTarget(targetType, targetId, deletedAt);
    upsertChunks(chunks);
  });
};

const embedAndStoreChunks = async (chunks: ChunkOutput[]) => {
  const embeddings = await embeddingProvider.embedBatch(chunks.map((chunk) => chunk.chunkText));
  await vectorStore.upsertBatch(
    chunks.map((chunk, index) => ({
      chunkId: chunk.id || '',
      textHash: chunk.textHash,
      embedding: embeddings[index].embedding,
      embeddingModel: embeddings[index].embeddingModel,
      metadata: {
        chunk_type: chunk.chunkType,
        chunk_index: chunk.chunkIndex
      }
    }))
  );
};

const finishJob = (jobId: string, targetType: string, targetId: string, chunkCount: number) => {
  completeEmbeddingJob(jobId, chunkCount);
  return {
    ok: true,
    job_id: jobId,
    target_type: targetType,
    target_id: targetId,
    chunk_count: chunkCount,
    embedding_model: embeddingProvider.embeddingModel
  } satisfies RebuildIndexResult;
};

export const rebuildNodeIndex = async (nodeId: string): Promise<RebuildIndexResult> => {
  const job = createEmbeddingJob({
    targetType: 'node',
    targetId: nodeId,
    embeddingModel: embeddingProvider.embeddingModel
  });

  try {
    const node = getNodeOrThrow(nodeId);
    const chunks = markdownChunker.chunk({
      targetType: 'node',
      targetId: node.id,
      title: node.title,
      content: node.content,
      metadata: {
        node_title: node.title,
        node_type: node.type
      }
    });
    replaceChunksForTarget('node', node.id, chunks);
    await embedAndStoreChunks(chunks);
    return finishJob(job.id, 'node', node.id, chunks.length);
  } catch (error) {
    failEmbeddingJob(job.id, errorMessage(error));
    throw error;
  }
};

export const rebuildCardIndex = async (cardId: string): Promise<RebuildIndexResult> => {
  const job = createEmbeddingJob({
    targetType: 'card',
    targetId: cardId,
    embeddingModel: embeddingProvider.embeddingModel
  });

  try {
    const card = getCardOrThrow(cardId);
    let rawJson: unknown;
    try {
      rawJson = JSON.parse(card.raw_json);
    } catch {
      throw new ApiError('CARD_JSON_INVALID', 'Card raw_json is invalid', 500);
    }

    const chunks = cardAwareChunker.chunk({
      targetType: 'card',
      targetId: card.id,
      title: card.display_title,
      rawJson,
      metadata: {
        card_id: card.id,
        node_id: card.node_id,
        display_title: card.display_title
      }
    });
    replaceChunksForTarget('card', card.id, chunks);
    await embedAndStoreChunks(chunks);
    return finishJob(job.id, 'card', card.id, chunks.length);
  } catch (error) {
    failEmbeddingJob(job.id, errorMessage(error));
    throw error;
  }
};

const normalizeTargetTypes = (value: unknown) => {
  const raw = Array.isArray(value) ? value : ['node', 'card'];
  const types = new Set(
    raw
      .map((item) => String(item).trim().toLowerCase())
      .filter((item) => item === 'node' || item === 'card')
  );
  return types.size > 0 ? types : new Set(['node', 'card']);
};

const normalizeBatchLimit = (value: unknown) => {
  if (value === undefined || value === null || value === '') {
    return null;
  }
  const parsed = Number.parseInt(String(value), 10);
  return Number.isFinite(parsed) && parsed > 0 ? Math.min(parsed, 10000) : null;
};

const listActiveCardIds = () =>
  getDb()
    .prepare(
      `
        SELECT id
        FROM cards
        WHERE deleted_at IS NULL
        ORDER BY updated_at DESC, created_at DESC
      `
    )
    .all() as Array<{ id: string }>;

export const rebuildAllIndexes = async (
  input: RebuildAllIndexesInput = {}
): Promise<RebuildAllIndexesResult> => {
  const targetTypes = normalizeTargetTypes(input.targetTypes);
  const limit = normalizeBatchLimit(input.limit);
  const targets: Array<{ target_type: 'node' | 'card'; target_id: string }> = [];

  if (targetTypes.has('node')) {
    targets.push(
      ...getDb()
        .prepare(
          `
            SELECT id
            FROM nodes
            WHERE deleted_at IS NULL
            ORDER BY updated_at DESC, created_at DESC
          `
        )
        .all()
        .map((row) => ({ target_type: 'node' as const, target_id: (row as { id: string }).id }))
    );
  }

  if (targetTypes.has('card')) {
    targets.push(...listActiveCardIds().map((row) => ({ target_type: 'card' as const, target_id: row.id })));
  }

  const limitedTargets = limit ? targets.slice(0, limit) : targets;
  let nodeCount = 0;
  let cardCount = 0;
  let chunkCount = 0;
  const errors: RebuildAllIndexesResult['errors'] = [];

  for (const target of limitedTargets) {
    try {
      const result =
        target.target_type === 'node'
          ? await rebuildNodeIndex(target.target_id)
          : await rebuildCardIndex(target.target_id);
      chunkCount += result.chunk_count;
      if (target.target_type === 'node') {
        nodeCount += 1;
      } else {
        cardCount += 1;
      }
    } catch (error) {
      errors.push({
        target_type: target.target_type,
        target_id: target.target_id,
        message: errorMessage(error)
      });
    }
  }

  return {
    ok: true,
    rebuilt: {
      nodes: nodeCount,
      cards: cardCount
    },
    target_count: limitedTargets.length,
    chunk_count: chunkCount,
    embedding_model: embeddingProvider.embeddingModel,
    vector_store: vectorStore.vectorStore,
    errors
  };
};
