import { createEmbeddingProvider } from './embeddingProviderFactory';
import { createVectorStore } from './vectorStoreFactory';

export const embeddingProvider = createEmbeddingProvider();
export const vectorStore = createVectorStore();
