import { createHash } from 'node:crypto';
import type { ChunkInput, ChunkOutput } from './chunkTypes';

export const normalizeChunkText = (text: string) => text.replace(/\r\n/g, '\n').trim();

export const hashText = (text: string) =>
  createHash('sha256').update(normalizeChunkText(text), 'utf8').digest('hex');

export const makeStableId = (prefix: string, parts: Array<string | number | null | undefined>) =>
  `${prefix}_${createHash('sha256')
    .update(parts.map((part) => (part === null || part === undefined ? '' : String(part))).join(':'))
    .digest('hex')
    .slice(0, 32)}`;

export const estimateTokenCount = (text: string) => {
  const cjkCount = (text.match(/[\u3400-\u9fff]/g) || []).length;
  const nonCjkText = text.replace(/[\u3400-\u9fff]/g, ' ');
  const wordCount = (nonCjkText.match(/[A-Za-z0-9_]+/g) || []).length;
  const symbolEstimate = Math.ceil(nonCjkText.replace(/[A-Za-z0-9_\s]/g, '').length / 2);
  return Math.max(1, cjkCount + wordCount + symbolEstimate);
};

export const makeChunk = (
  input: ChunkInput,
  chunk: {
    sourceTable?: string;
    chunkType: string;
    chunkText: string;
    chunkIndex: number;
    metadata?: Record<string, unknown>;
  }
): ChunkOutput | null => {
  const chunkText = normalizeChunkText(chunk.chunkText);
  if (!chunkText) {
    return null;
  }

  const textHash = hashText(chunkText);
  return {
    id: makeStableId('chunk', [
      input.targetType,
      input.targetId,
      chunk.chunkType,
      chunk.chunkIndex,
      textHash
    ]),
    targetType: input.targetType,
    targetId: input.targetId,
    sourceTable: chunk.sourceTable,
    chunkType: chunk.chunkType,
    chunkText,
    chunkIndex: chunk.chunkIndex,
    textHash,
    tokenCount: estimateTokenCount(chunkText),
    metadata: {
      ...(input.metadata || {}),
      ...(chunk.metadata || {})
    }
  };
};
