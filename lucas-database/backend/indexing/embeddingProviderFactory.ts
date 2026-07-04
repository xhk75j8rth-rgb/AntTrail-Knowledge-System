import { ApiError } from '../errors';
import type { EmbeddingProvider } from './embeddingProvider';
import { BgeM3EmbeddingProvider, bgeM3EmbeddingDim, bgeM3EmbeddingModel } from './bgeM3EmbeddingProvider';
import { MockEmbeddingProvider, mockEmbeddingDim, mockEmbeddingModel } from './mockEmbeddingProvider';

export type EmbeddingProviderName = 'mock' | 'bge-m3';

const normalizeProviderName = (value: string | undefined): EmbeddingProviderName => {
  const normalized = (value || 'mock').trim().toLowerCase();
  if (normalized === 'mock' || normalized === 'mock-embedding-v0') {
    return 'mock';
  }
  if (normalized === 'bge-m3' || normalized === 'bge_m3' || normalized === 'bge') {
    return 'bge-m3';
  }
  throw new ApiError(
    'EMBEDDING_PROVIDER_INVALID',
    `Unsupported embedding provider: ${value}. Use "mock" or "bge-m3".`,
    500
  );
};

export const getConfiguredEmbeddingProviderName = () =>
  normalizeProviderName(process.env.LUCAS_EMBEDDING_PROVIDER);

export const getEmbeddingProviderDescriptor = () => {
  const providerName = getConfiguredEmbeddingProviderName();
  if (providerName === 'bge-m3') {
    return {
      provider: providerName,
      embedding_model: process.env.LUCAS_BGE_M3_MODEL || bgeM3EmbeddingModel,
      embedding_dim: bgeM3EmbeddingDim
    };
  }

  return {
    provider: providerName,
    embedding_model: mockEmbeddingModel,
    embedding_dim: mockEmbeddingDim
  };
};

export const createEmbeddingProvider = (): EmbeddingProvider => {
  const providerName = getConfiguredEmbeddingProviderName();
  if (providerName === 'bge-m3') {
    return new BgeM3EmbeddingProvider();
  }
  return new MockEmbeddingProvider();
};
