import { ApiError } from '../errors';
import type { VectorStore } from './vectorStore';
import { MockVectorStore } from './mockVectorStore';
import { SqliteVecStore } from './sqliteVecStore';

export type VectorStoreName = 'mock' | 'sqlite-vec';

const normalizeVectorStoreName = (value: string | undefined): VectorStoreName => {
  const normalized = (value || 'mock').trim().toLowerCase();
  if (normalized === 'mock') {
    return 'mock';
  }
  if (normalized === 'sqlite-vec' || normalized === 'sqlite_vec' || normalized === 'sqlitevec') {
    return 'sqlite-vec';
  }
  throw new ApiError(
    'VECTOR_STORE_INVALID',
    `Unsupported vector store: ${value}. Use "mock" or "sqlite-vec".`,
    500
  );
};

export const getConfiguredVectorStoreName = () => normalizeVectorStoreName(process.env.LUCAS_VECTOR_STORE);

export const getVectorStoreDescriptor = () => {
  const vectorStore = getConfiguredVectorStoreName();
  return {
    vector_store: vectorStore
  };
};

export const createVectorStore = (): VectorStore => {
  const vectorStore = getConfiguredVectorStoreName();
  if (vectorStore === 'sqlite-vec') {
    return new SqliteVecStore();
  }
  return new MockVectorStore();
};
