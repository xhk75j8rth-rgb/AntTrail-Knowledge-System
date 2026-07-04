import { ApiError } from '../errors';
import { searchHybridRetrieval } from './retrievalService';

export interface BuildContextInput {
  query: unknown;
  limit?: unknown;
  token_budget?: unknown;
  max_chunks_per_source?: unknown;
}

const normalizeQuery = (value: unknown) => {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new ApiError('CONTEXT_QUERY_REQUIRED', 'query is required', 400);
  }
  return value.trim();
};

const normalizePositiveInt = (value: unknown, fallback: number, min: number, max: number) => {
  const parsed = typeof value === 'number' ? value : Number.parseInt(String(value || ''), 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(min, Math.min(Math.trunc(parsed), max));
};

const estimateTokens = (text: string) => Math.max(1, Math.ceil(text.length / 2));

const truncateToTokenBudget = (text: string, tokenBudget: number) => {
  const estimated = estimateTokens(text);
  if (estimated <= tokenBudget) {
    return text;
  }
  const charBudget = Math.max(20, Math.max(1, tokenBudget - 5) * 2);
  return `${text.slice(0, charBudget).trim()}...`;
};

const sourceFromResult = (result: Awaited<ReturnType<typeof searchHybridRetrieval>>['results'][number]) => {
  if (result.card) {
    return {
      source_key: `card:${result.card.id}`,
      source_type: 'card',
      source_id: result.card.id,
      title: result.card.display_title,
      path: result.card.path || result.path
    };
  }

  if (result.node) {
    return {
      source_key: `node:${result.node.id}`,
      source_type: 'node',
      source_id: result.node.id,
      title: result.node.title,
      path: result.node.path || result.path
    };
  }

  return {
    source_key: `${result.chunk.target_type}:${result.chunk.target_id}`,
    source_type: result.chunk.target_type,
    source_id: result.chunk.target_id,
    title: result.path || result.chunk.target_id,
    path: result.path
  };
};

export const buildContext = async (input: BuildContextInput) => {
  const query = normalizeQuery(input.query);
  const limit = normalizePositiveInt(input.limit, 12, 1, 50);
  const tokenBudget = normalizePositiveInt(input.token_budget, 1800, 200, 12000);
  const maxChunksPerSource = normalizePositiveInt(input.max_chunks_per_source, 3, 1, 10);
  const retrieval = await searchHybridRetrieval({
    query,
    limit: Math.min(limit * 3, 50)
  });

  const seenChunkIds = new Set<string>();
  const seenTextHashes = new Set<string>();
  const sourceCounts = new Map<string, number>();
  const sourceIndexByKey = new Map<string, number>();
  const sources: Array<{
    source_index: number;
    source_key: string;
    source_type: string;
    source_id: string;
    title: string;
    path: string;
    chunk_ids: string[];
    best_score: number;
  }> = [];
  const blocks: Array<{
    source_index: number;
    chunk_id: string;
    title: string;
    path: string;
    text: string;
    estimated_tokens: number;
    hybrid_score: number;
    signals: unknown;
  }> = [];
  const omitted = {
    duplicate_chunks: 0,
    duplicate_text: 0,
    source_limit: 0,
    token_budget: 0
  };
  let usedTokens = 0;

  for (const result of retrieval.results) {
    if (blocks.length >= limit) {
      break;
    }

    if (seenChunkIds.has(result.chunk_id)) {
      omitted.duplicate_chunks += 1;
      continue;
    }
    seenChunkIds.add(result.chunk_id);

    const textHash = result.chunk.text_hash;
    if (textHash && seenTextHashes.has(textHash)) {
      omitted.duplicate_text += 1;
      continue;
    }
    if (textHash) {
      seenTextHashes.add(textHash);
    }

    const source = sourceFromResult(result);
    const currentSourceCount = sourceCounts.get(source.source_key) || 0;
    if (currentSourceCount >= maxChunksPerSource) {
      omitted.source_limit += 1;
      continue;
    }

    let sourceIndex = sourceIndexByKey.get(source.source_key);
    const isNewSource = !sourceIndex;
    sourceIndex = sourceIndex || sources.length + 1;

    const remainingTokens = tokenBudget - usedTokens;
    const prefix = `[Source ${sourceIndex}] ${source.title}\nPath: ${source.path}\nChunk: ${result.chunk.chunk_type} #${result.chunk.chunk_index}\n`;
    const prefixTokens = estimateTokens(prefix);
    if (remainingTokens <= prefixTokens + 20) {
      omitted.token_budget += 1;
      break;
    }

    const textBudget = remainingTokens - prefixTokens;
    let text = truncateToTokenBudget(result.chunk.chunk_text, textBudget);
    let blockText = `${prefix}${text}`;
    let blockTokens = estimateTokens(blockText);
    if (usedTokens + blockTokens > tokenBudget) {
      const smallerTextBudget = Math.max(20, tokenBudget - usedTokens - prefixTokens - 10);
      text = truncateToTokenBudget(result.chunk.chunk_text, smallerTextBudget);
      blockText = `${prefix}${text}`;
      blockTokens = estimateTokens(blockText);
    }
    if (usedTokens + blockTokens > tokenBudget) {
      omitted.token_budget += 1;
      break;
    }

    usedTokens += blockTokens;
    if (isNewSource) {
      sourceIndexByKey.set(source.source_key, sourceIndex);
      sources.push({
        source_index: sourceIndex,
        source_key: source.source_key,
        source_type: source.source_type,
        source_id: source.source_id,
        title: source.title,
        path: source.path,
        chunk_ids: [],
        best_score: result.hybrid_score
      });
    }
    sourceCounts.set(source.source_key, currentSourceCount + 1);
    const sourceRecord = sources[sourceIndex - 1];
    sourceRecord.chunk_ids.push(result.chunk_id);
    sourceRecord.best_score = Math.max(sourceRecord.best_score, result.hybrid_score);

    blocks.push({
      source_index: sourceIndex,
      chunk_id: result.chunk_id,
      title: source.title,
      path: source.path,
      text,
      estimated_tokens: blockTokens,
      hybrid_score: result.hybrid_score,
      signals: result.signals
    });
  }

  return {
    ok: true,
    query,
    retrieval_type: 'context',
    token_budget: tokenBudget,
    estimated_tokens: usedTokens,
    confidence: retrieval.confidence,
    retrieval: {
      type: retrieval.retrieval_type,
      fusion: retrieval.fusion,
      candidate_count: retrieval.results.length
    },
    sources,
    blocks,
    omitted,
    context_text: blocks.map((block) => block.text).join('\n\n---\n\n')
  };
};
