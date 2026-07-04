import { ApiError } from '../errors';
import { getCardOrThrow } from '../cardsRepo';
import { getDb } from '../db';
import { getNodeById } from '../nodesRepo';
import { getChunkOrThrow } from './chunkRepo';
import { embeddingProvider, vectorStore } from './indexingRuntime';
import type { ChunkRow } from './chunkTypes';
import type { VectorStoreSearchResult } from './vectorStore';

export interface VectorRetrievalInput {
  query: unknown;
  limit?: unknown;
}

const parseJsonObject = (value: string | null) => {
  if (!value) {
    return {};
  }
  try {
    const parsed = JSON.parse(value);
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
};

const normalizeLimit = (value: unknown) => {
  const parsed = typeof value === 'number' ? value : Number.parseInt(String(value || ''), 10);
  if (!Number.isFinite(parsed)) {
    return 10;
  }
  return Math.max(1, Math.min(Math.trunc(parsed), 50));
};

const normalizeQuery = (value: unknown) => {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new ApiError('RETRIEVAL_QUERY_REQUIRED', 'query is required', 400);
  }
  return value.trim();
};

const configuredLowConfidenceThreshold = (embeddingDim: number) => {
  const raw = process.env.LUCAS_VECTOR_LOW_CONFIDENCE_DISTANCE;
  if (raw !== undefined) {
    const parsed = Number.parseFloat(raw);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }
  return embeddingDim >= 100 ? 0.9 : null;
};

const makeConfidence = (results: Array<{ distance: number }>, embeddingDim: number) => {
  const bestDistance = results[0]?.distance ?? null;
  const threshold = configuredLowConfidenceThreshold(embeddingDim);
  if (results.length === 0) {
    return {
      low_confidence: true,
      reason: 'no_results',
      best_distance: null,
      threshold
    };
  }
  if (threshold !== null && bestDistance !== null && bestDistance > threshold) {
    return {
      low_confidence: true,
      reason: 'distance_above_threshold',
      best_distance: bestDistance,
      threshold
    };
  }
  return {
    low_confidence: false,
    reason: null,
    best_distance: bestDistance,
    threshold
  };
};

const buildNodePath = (nodeId: string) => {
  const segments: string[] = [];
  let currentId: string | null = nodeId;

  while (currentId) {
    const node = getNodeById(currentId);
    if (!node) {
      break;
    }
    segments.unshift(node.title);
    currentId = node.parent_id;
  }

  return segments.length > 0 ? `/${segments.join('/')}` : '';
};

const serializeChunk = (chunk: ChunkRow) => ({
  id: chunk.id,
  target_type: chunk.target_type,
  target_id: chunk.target_id,
  source_table: chunk.source_table,
  chunk_type: chunk.chunk_type,
  chunk_text: chunk.chunk_text,
  chunk_index: chunk.chunk_index,
  text_hash: chunk.text_hash,
  token_count: chunk.token_count,
  metadata: parseJsonObject(chunk.metadata_json),
  created_at: chunk.created_at,
  updated_at: chunk.updated_at
});

const hydrateTarget = (chunk: ChunkRow) => {
  if (chunk.target_type === 'node') {
    const node = getNodeById(chunk.target_id);
    if (!node) {
      return {
        path: '',
        node: null,
        card: null
      };
    }

    return {
      path: buildNodePath(node.id),
      node: {
        id: node.id,
        title: node.title,
        type: node.type,
        path: buildNodePath(node.id)
      },
      card: null
    };
  }

  if (chunk.target_type === 'card') {
    try {
      const card = getCardOrThrow(chunk.target_id);
      const node = getNodeById(card.node_id);
      const path = node ? buildNodePath(node.id) : '';
      return {
        path,
        node: node
          ? {
              id: node.id,
              title: node.title,
              type: node.type,
              path
            }
          : null,
        card: {
          id: card.id,
          node_id: card.node_id,
          display_title: card.display_title,
          card_type: card.card_type,
          path
        }
      };
    } catch {
      return {
        path: '',
        node: null,
        card: null
      };
    }
  }

  return {
    path: '',
    node: null,
    card: null
  };
};

const hydrateResult = (result: VectorStoreSearchResult) => {
  try {
    const chunk = getChunkOrThrow(result.chunkId);
    const target = hydrateTarget(chunk);
    return {
      chunk_id: result.chunkId,
      distance: result.distance,
      vector_store: result.vectorStore,
      vector_id: result.vectorId,
      embedding_model: result.embeddingModel,
      embedding_dim: result.embeddingDim,
      path: target.path,
      chunk: serializeChunk(chunk),
      node: target.node,
      card: target.card,
      metadata: {
        chunk: parseJsonObject(chunk.metadata_json),
        vector: result.metadata || {}
      }
    };
  } catch {
    return null;
  }
};

export const searchVectorRetrieval = async (input: VectorRetrievalInput) => {
  const query = normalizeQuery(input.query);
  const limit = normalizeLimit(input.limit);
  const [queryEmbedding] = await embeddingProvider.embedBatch([query]);
  const matches = await vectorStore.search({
    embedding: queryEmbedding.embedding,
    embeddingModel: queryEmbedding.embeddingModel,
    limit
  });
  const results = matches.map(hydrateResult).filter((item): item is NonNullable<typeof item> => item !== null);

  return {
    ok: true,
    query,
    limit,
    embedding_provider: {
      embedding_model: queryEmbedding.embeddingModel,
      embedding_dim: queryEmbedding.embeddingDim
    },
    vector_store: {
      vector_store: vectorStore.vectorStore
    },
    confidence: makeConfidence(results, queryEmbedding.embeddingDim),
    results
  };
};

const keywordTerms = (query: string) => {
  const lowerQuery = query.toLowerCase();
  const stopwords = new Set(['内容', '不存在', '什么', '怎么', '如何', '需要', '哪些', '一个']);
  const terms = lowerQuery
    .split(/[^\p{L}\p{N}]+/u)
    .map((term) => term.trim())
    .filter((term) => term.length >= 2 && !stopwords.has(term));
  return Array.from(new Set([lowerQuery, ...terms])).slice(0, 16);
};

const includesTerm = (value: string | null | undefined, term: string) =>
  (value || '').toLowerCase().includes(term);

const keywordScore = (input: {
  query: string;
  terms: string[];
  chunkText: string;
  nodeTitle?: string | null;
  cardTitle?: string | null;
}) => {
  let score = 0;
  if (includesTerm(input.nodeTitle, input.query)) {
    score += 120;
  }
  if (includesTerm(input.cardTitle, input.query)) {
    score += 120;
  }
  if (includesTerm(input.chunkText, input.query)) {
    score += 80;
  }

  for (const term of input.terms) {
    if (term === input.query) {
      continue;
    }
    if (includesTerm(input.nodeTitle, term)) {
      score += 24;
    }
    if (includesTerm(input.cardTitle, term)) {
      score += 24;
    }
    if (includesTerm(input.chunkText, term)) {
      score += 8;
    }
  }

  return score;
};

export const searchKeywordRetrieval = async (input: VectorRetrievalInput) => {
  const query = normalizeQuery(input.query);
  const limit = normalizeLimit(input.limit);
  const terms = keywordTerms(query);
  const rows = getDb()
    .prepare(
      `
        SELECT
          c.id,
          c.chunk_text,
          n.title AS node_title,
          card.display_title AS card_title
        FROM chunks c
        LEFT JOIN nodes n
          ON c.target_type = 'node'
         AND c.target_id = n.id
         AND n.deleted_at IS NULL
        LEFT JOIN cards card
          ON c.target_type = 'card'
         AND c.target_id = card.id
         AND card.deleted_at IS NULL
        WHERE c.deleted_at IS NULL
      `
    )
    .all() as Array<{
    id: string;
    chunk_text: string;
    node_title: string | null;
    card_title: string | null;
  }>;

  const scored = rows
    .map((row) => ({
      row,
      score: keywordScore({
        query: query.toLowerCase(),
        terms,
        chunkText: row.chunk_text,
        nodeTitle: row.node_title,
        cardTitle: row.card_title
      })
    }))
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score)
    .slice(0, limit);

  const results = scored
    .map((item) => {
      try {
        const chunk = getChunkOrThrow(item.row.id);
        const target = hydrateTarget(chunk);
        return {
          chunk_id: chunk.id,
          score: item.score,
          path: target.path,
          chunk: serializeChunk(chunk),
          node: target.node,
          card: target.card,
          metadata: {
            chunk: parseJsonObject(chunk.metadata_json)
          }
        };
      } catch {
        return null;
      }
    })
    .filter((item): item is NonNullable<typeof item> => item !== null);
  const bestScore = results[0]?.score ?? null;
  const keywordThreshold = (() => {
    const parsed = Number.parseFloat(process.env.LUCAS_KEYWORD_LOW_CONFIDENCE_SCORE || '');
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 24;
  })();

  return {
    ok: true,
    query,
    limit,
    retrieval_type: 'keyword',
    confidence: {
      low_confidence: results.length === 0 || (bestScore !== null && bestScore < keywordThreshold),
      reason:
        results.length === 0
          ? 'no_results'
          : bestScore !== null && bestScore < keywordThreshold
            ? 'score_below_threshold'
            : null,
      best_score: bestScore,
      threshold: keywordThreshold
    },
    results
  };
};

const configuredWeight = (value: string | undefined, fallback: number) => {
  const parsed = Number.parseFloat(value || '');
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
};

const hybridWeights = () => {
  const vectorWeight = configuredWeight(process.env.LUCAS_HYBRID_VECTOR_WEIGHT, 0.65);
  const keywordWeight = configuredWeight(process.env.LUCAS_HYBRID_KEYWORD_WEIGHT, 0.35);
  const total = vectorWeight + keywordWeight || 1;
  return {
    vector: vectorWeight / total,
    keyword: keywordWeight / total
  };
};

const hybridLowConfidenceThreshold = () => {
  const parsed = Number.parseFloat(process.env.LUCAS_HYBRID_LOW_CONFIDENCE_SCORE || '');
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0.35;
};

const roundScore = (value: number) => Number(value.toFixed(6));

export const searchHybridRetrieval = async (input: VectorRetrievalInput) => {
  const query = normalizeQuery(input.query);
  const limit = normalizeLimit(input.limit);
  const candidateLimit = Math.min(Math.max(limit * 3, limit), 50);
  const [vectorResult, keywordResult] = await Promise.all([
    searchVectorRetrieval({ query, limit: candidateLimit }),
    searchKeywordRetrieval({ query, limit: candidateLimit })
  ]);
  const weights = hybridWeights();
  const entries = new Map<
    string,
    {
      base: (typeof vectorResult.results)[number] | (typeof keywordResult.results)[number];
      vector?: {
        rank: number;
        distance: number;
        raw_score: number;
        normalized_score: number;
      };
      keyword?: {
        rank: number;
        score: number;
        normalized_score: number;
        exact_match: boolean;
      };
    }
  >();

  const vectorRawScores = vectorResult.results.map((item) => 1 / (1 + item.distance));
  const maxVectorRawScore = Math.max(...vectorRawScores, 0);
  vectorResult.results.forEach((item, index) => {
    const rawScore = vectorRawScores[index] || 0;
    const entry = entries.get(item.chunk_id) || { base: item };
    entry.vector = {
      rank: index + 1,
      distance: item.distance,
      raw_score: rawScore,
      normalized_score: maxVectorRawScore > 0 ? rawScore / maxVectorRawScore : 0
    };
    entries.set(item.chunk_id, entry);
  });

  const maxKeywordScore = Math.max(...keywordResult.results.map((item) => item.score), 0);
  keywordResult.results.forEach((item, index) => {
    const entry = entries.get(item.chunk_id) || { base: item };
    entry.keyword = {
      rank: index + 1,
      score: item.score,
      normalized_score: maxKeywordScore > 0 ? item.score / maxKeywordScore : 0,
      exact_match: item.score >= 80
    };
    entries.set(item.chunk_id, entry);
  });

  const vectorReliability = vectorResult.confidence.low_confidence ? 0 : 1;
  const results = Array.from(entries.values())
    .map((entry) => {
      const vectorScore = (entry.vector?.normalized_score || 0) * vectorReliability;
      const keywordScore = entry.keyword?.normalized_score || 0;
      const exactKeywordBoost = entry.keyword?.exact_match ? weights.keyword : 0;
      const hybridScore = Math.min(
        1,
        weights.vector * vectorScore + weights.keyword * keywordScore + exactKeywordBoost
      );
      return {
        chunk_id: entry.base.chunk_id,
        hybrid_score: roundScore(hybridScore),
        path: entry.base.path,
        chunk: entry.base.chunk,
        node: entry.base.node,
        card: entry.base.card,
        metadata: entry.base.metadata,
        signals: {
          vector: entry.vector
            ? {
                rank: entry.vector.rank,
                distance: roundScore(entry.vector.distance),
                score: roundScore(entry.vector.normalized_score)
              }
            : null,
          keyword: entry.keyword
            ? {
                rank: entry.keyword.rank,
                score: entry.keyword.score,
                normalized_score: roundScore(entry.keyword.normalized_score),
                exact_match: entry.keyword.exact_match,
                exact_boost: entry.keyword.exact_match ? roundScore(exactKeywordBoost) : 0
              }
            : null
        }
      };
    })
    .sort((left, right) => right.hybrid_score - left.hybrid_score)
    .slice(0, limit);

  const bestScore = results[0]?.hybrid_score ?? null;
  const threshold = hybridLowConfidenceThreshold();
  const signalLowConfidence = vectorResult.confidence.low_confidence && keywordResult.confidence.low_confidence;
  const scoreLowConfidence = bestScore !== null && bestScore < threshold;
  const lowConfidence = results.length === 0 || signalLowConfidence || scoreLowConfidence;
  const reason =
    results.length === 0
      ? 'no_results'
      : signalLowConfidence
        ? 'supporting_signal_low_confidence'
        : scoreLowConfidence
          ? 'score_below_threshold'
          : null;

  return {
    ok: true,
    query,
    limit,
    retrieval_type: 'hybrid',
    fusion: {
      vector_weight: weights.vector,
      keyword_weight: weights.keyword
    },
    confidence: {
      low_confidence: lowConfidence,
      reason,
      best_score: bestScore,
      threshold,
      vector: vectorResult.confidence,
      keyword: keywordResult.confidence
    },
    results
  };
};
