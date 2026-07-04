import { Type } from 'typebox';
import { defineToolPlugin } from 'openclaw/plugin-sdk/tool-plugin';

const DEFAULT_BASE_URL = 'http://127.0.0.1:8765';

const configSchema = Type.Object({
  apiBaseUrl: Type.Optional(Type.String({
    description: 'Lucas Database API base URL. Defaults to LUCAS_AGENT_RETRIEVE_BASE_URL, LUCAS_DB_API_BASE_URL, or http://127.0.0.1:8765.'
  })),
  apiToken: Type.Optional(Type.String({
    description: 'Optional bearer token if the local Lucas read API is protected in a future deployment.'
  })),
  defaultLimit: Type.Optional(Type.Integer({
    minimum: 1,
    maximum: 50,
    description: 'Default retrieval result limit.'
  })),
  defaultTokenBudget: Type.Optional(Type.Integer({
    minimum: 200,
    maximum: 12000,
    description: 'Default context token budget.'
  })),
  defaultMaxChunksPerSource: Type.Optional(Type.Integer({
    minimum: 1,
    maximum: 10,
    description: 'Default maximum accepted chunks per source.'
  })),
  timeoutMs: Type.Optional(Type.Integer({
    minimum: 1000,
    maximum: 300000,
    description: 'HTTP timeout for one Lucas retrieval call.'
  }))
});

type LucasConfig = {
  apiBaseUrl?: string;
  apiToken?: string;
  defaultLimit?: number;
  defaultTokenBudget?: number;
  defaultMaxChunksPerSource?: number;
  timeoutMs?: number;
};

type LucasRetrieveParams = {
  query: string;
  limit?: number;
  token_budget?: number;
  max_chunks_per_source?: number;
  response_format?: 'default' | 'messages';
};

const clampInt = (value: unknown, fallback: number, min: number, max: number) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return fallback;
  }
  return Math.max(min, Math.min(Math.trunc(value), max));
};

const normalizeBaseUrl = (config: LucasConfig) => {
  const raw =
    config.apiBaseUrl ||
    process.env.LUCAS_AGENT_RETRIEVE_BASE_URL ||
    process.env.LUCAS_DB_API_BASE_URL ||
    DEFAULT_BASE_URL;
  return raw.replace(/\/+$/, '');
};

const resolveToken = (config: LucasConfig) =>
  config.apiToken || process.env.LUCAS_AGENT_RETRIEVE_TOKEN || process.env.LUCAS_DB_API_TOKEN || '';

const makeAbortSignal = (timeoutMs: number, parentSignal?: AbortSignal) => {
  const controller = new AbortController();
  const timeout = setTimeout(() => {
    controller.abort(new Error(`Lucas retrieval timed out after ${timeoutMs}ms`));
  }, timeoutMs);
  const abortFromParent = () => controller.abort(parentSignal?.reason);

  if (parentSignal?.aborted) {
    abortFromParent();
  } else {
    parentSignal?.addEventListener('abort', abortFromParent, { once: true });
  }

  return {
    signal: controller.signal,
    cleanup: () => {
      clearTimeout(timeout);
      parentSignal?.removeEventListener('abort', abortFromParent);
    }
  };
};

const parseJsonResponse = async (response: Response) => {
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { raw_text: text };
  }
};

const errorMessage = (error: unknown) => {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
};

const callLucasRetrieve = async (
  params: LucasRetrieveParams,
  config: LucasConfig,
  parentSignal?: AbortSignal
) => {
  const query = params.query.trim();
  if (!query) {
    return {
      ok: false,
      error: {
        code: 'LUCAS_QUERY_REQUIRED',
        message: 'query is required'
      }
    };
  }

  const baseUrl = normalizeBaseUrl(config);
  const timeoutMs = clampInt(config.timeoutMs, 60000, 1000, 300000);
  const limit = clampInt(params.limit, clampInt(config.defaultLimit, 6, 1, 50), 1, 50);
  const tokenBudget = clampInt(
    params.token_budget,
    clampInt(config.defaultTokenBudget, 1800, 200, 12000),
    200,
    12000
  );
  const maxChunksPerSource = clampInt(
    params.max_chunks_per_source,
    clampInt(config.defaultMaxChunksPerSource, 2, 1, 10),
    1,
    10
  );
  const body = {
    query,
    limit,
    token_budget: tokenBudget,
    max_chunks_per_source: maxChunksPerSource,
    agent: 'openclaw',
    response_format: params.response_format === 'messages' ? 'messages' : 'default'
  };
  const token = resolveToken(config);
  const { signal, cleanup } = makeAbortSignal(timeoutMs, parentSignal);

  try {
    const response = await fetch(`${baseUrl}/api/agent/retrieve`, {
      method: 'POST',
      signal,
      headers: {
        'content-type': 'application/json',
        ...(token ? { authorization: `Bearer ${token}` } : {})
      },
      body: JSON.stringify(body)
    });
    const payload = await parseJsonResponse(response);
    if (!response.ok) {
      return {
        ok: false,
        error: {
          code: 'LUCAS_HTTP_ERROR',
          status: response.status,
          message: response.statusText || 'Lucas retrieval HTTP error',
          body: payload
        }
      };
    }
    return payload;
  } catch (error) {
    return {
      ok: false,
      error: {
        code: 'LUCAS_RETRIEVE_FAILED',
        message: errorMessage(error)
      }
    };
  } finally {
    cleanup();
  }
};

export default defineToolPlugin({
  id: 'lucas-retrieval',
  name: 'Lucas Retrieval',
  description: 'Retrieve grounded context from the local Lucas Database Agent Retrieve API.',
  configSchema,
  tools: (tool) => [
    tool({
      name: 'lucas_retrieve',
      label: 'Lucas Retrieve',
      description:
        'Retrieve grounded context, sources, citations, and answerability from Lucas Database. Use before answering questions about the user saved notes, cards, nodes, project history, or local knowledge base.',
      parameters: Type.Object({
        query: Type.String({
          description: 'Question or search intent to retrieve from Lucas Database.'
        }),
        limit: Type.Optional(Type.Integer({
          minimum: 1,
          maximum: 50,
          description: 'Maximum context blocks to return.'
        })),
        token_budget: Type.Optional(Type.Integer({
          minimum: 200,
          maximum: 12000,
          description: 'Approximate token budget for returned context.'
        })),
        max_chunks_per_source: Type.Optional(Type.Integer({
          minimum: 1,
          maximum: 10,
          description: 'Maximum chunks accepted from one source.'
        })),
        response_format: Type.Optional(Type.Union([
          Type.Literal('default'),
          Type.Literal('messages')
        ], {
          description: 'Use messages when the caller wants a system/user prompt pair in the response.'
        }))
      }),
      async execute(params, config, context) {
        context.signal?.throwIfAborted();
        return callLucasRetrieve(params, config, context.signal);
      }
    })
  ]
});
