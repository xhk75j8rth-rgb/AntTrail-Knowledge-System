export interface VectorStoreUpsertInput {
  chunkId: string;
  textHash: string;
  embedding: number[];
  embeddingModel: string;
  metadata?: Record<string, unknown>;
}

export interface VectorStoreUpsertResult {
  chunkId: string;
  vectorStore: string;
  vectorId: string;
  embeddingModel: string;
  embeddingDim: number;
}

export interface VectorStoreSearchInput {
  embedding: number[];
  embeddingModel: string;
  limit: number;
}

export interface VectorStoreSearchResult {
  chunkId: string;
  vectorStore: string;
  vectorId: string;
  embeddingModel: string;
  embeddingDim: number;
  distance: number;
  metadata?: Record<string, unknown>;
}

export interface VectorStore {
  vectorStore: string;
  upsertBatch(records: VectorStoreUpsertInput[]): Promise<VectorStoreUpsertResult[]>;
  search(input: VectorStoreSearchInput): Promise<VectorStoreSearchResult[]>;
}
