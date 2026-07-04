import { makeStableId } from './chunker';
import { listActiveVectorsByStore, upsertEmbeddingVector } from './embeddingVectorRepo';
import type {
  VectorStore,
  VectorStoreSearchInput,
  VectorStoreSearchResult,
  VectorStoreUpsertInput,
  VectorStoreUpsertResult
} from './vectorStore';

const parseMetadataJson = (value: string | null) => {
  if (!value) {
    return {};
  }
  try {
    const parsed = JSON.parse(value);
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
};

const asNumberArray = (value: unknown) =>
  Array.isArray(value) && value.every((item) => typeof item === 'number')
    ? (value as number[])
    : [];

const euclideanDistance = (left: number[], right: number[]) => {
  const length = Math.min(left.length, right.length);
  if (length === 0) {
    return Number.POSITIVE_INFINITY;
  }
  let sum = 0;
  for (let index = 0; index < length; index += 1) {
    const diff = left[index] - right[index];
    sum += diff * diff;
  }
  return Math.sqrt(sum);
};

export class MockVectorStore implements VectorStore {
  vectorStore = 'mock';

  async upsertBatch(records: VectorStoreUpsertInput[]): Promise<VectorStoreUpsertResult[]> {
    const now = new Date().toISOString();
    return records.map((record) => {
      const vectorId = makeStableId('mock_vector', [
        record.chunkId,
        record.textHash,
        record.embeddingModel
      ]);
      const id = makeStableId('emb_vec', [this.vectorStore, vectorId]);
      const embeddingDim = record.embedding.length;
      const embeddingPreview = record.embedding.slice(0, 8);

      upsertEmbeddingVector(
        {
          id,
          chunkId: record.chunkId,
          vectorStore: this.vectorStore,
          vectorId,
          embeddingModel: record.embeddingModel,
          embeddingDim,
          textHash: record.textHash,
          metadata: {
            ...(record.metadata || {}),
            embedding_preview: embeddingPreview,
            embedding_preview_dim: embeddingPreview.length
          }
        },
        now
      );

      return {
        chunkId: record.chunkId,
        vectorStore: this.vectorStore,
        vectorId,
        embeddingModel: record.embeddingModel,
        embeddingDim
      };
    });
  }

  async search(input: VectorStoreSearchInput): Promise<VectorStoreSearchResult[]> {
    const rows = listActiveVectorsByStore({
      vectorStore: this.vectorStore,
      embeddingModel: input.embeddingModel,
      embeddingDim: input.embedding.length
    });

    return rows
      .map((row) => {
        const metadata = parseMetadataJson(row.metadata_json);
        const candidate =
          asNumberArray(metadata.embedding_preview).length > 0
            ? asNumberArray(metadata.embedding_preview)
            : asNumberArray(metadata.mock_vector_preview);
        return {
          chunkId: row.chunk_id,
          vectorStore: row.vector_store,
          vectorId: row.vector_id,
          embeddingModel: row.embedding_model,
          embeddingDim: row.embedding_dim,
          distance: euclideanDistance(input.embedding, candidate),
          metadata
        };
      })
      .filter((result) => Number.isFinite(result.distance))
      .sort((left, right) => left.distance - right.distance)
      .slice(0, input.limit);
  }
}
