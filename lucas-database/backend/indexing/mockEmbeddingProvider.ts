import { createHash } from 'node:crypto';
import type { EmbeddingProvider, EmbeddingResult } from './embeddingProvider';

export const mockEmbeddingModel = 'mock-embedding-v0';
export const mockEmbeddingDim = 8;

const hashBytes = (text: string) => createHash('sha256').update(text, 'utf8').digest();

const makeMockVector = (text: string) => {
  const bytes = hashBytes(text);
  const vector = Array.from({ length: mockEmbeddingDim }, (_, index) => {
    const byte = bytes[index];
    return Number(((byte / 255) * 2 - 1).toFixed(6));
  });
  const length = Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0)) || 1;
  return vector.map((value) => Number((value / length).toFixed(6)));
};

export class MockEmbeddingProvider implements EmbeddingProvider {
  embeddingModel = mockEmbeddingModel;
  embeddingDim = mockEmbeddingDim;

  async embedBatch(texts: string[]): Promise<EmbeddingResult[]> {
    return texts.map((text) => ({
      text,
      embedding: makeMockVector(text),
      embeddingModel: this.embeddingModel,
      embeddingDim: this.embeddingDim
    }));
  }
}
