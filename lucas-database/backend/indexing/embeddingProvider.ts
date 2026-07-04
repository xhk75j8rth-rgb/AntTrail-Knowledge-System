export interface EmbeddingResult {
  text: string;
  embedding: number[];
  embeddingModel: string;
  embeddingDim: number;
}

export interface EmbeddingProvider {
  embeddingModel: string;
  embeddingDim: number;
  embedBatch(texts: string[]): Promise<EmbeddingResult[]>;
}
