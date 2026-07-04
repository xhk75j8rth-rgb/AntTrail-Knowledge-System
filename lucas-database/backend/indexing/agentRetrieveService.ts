import { buildContext, type BuildContextInput } from './contextBuilderService';

export interface AgentRetrieveInput extends BuildContextInput {
  agent?: unknown;
  response_format?: unknown;
}

const normalizeAgentName = (value: unknown) => {
  if (typeof value !== 'string') {
    return null;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed.slice(0, 80) : null;
};

const normalizeResponseFormat = (value: unknown) => {
  if (typeof value !== 'string') {
    return 'default';
  }
  const normalized = value.trim().toLowerCase();
  return normalized === 'messages' ? 'messages' : 'default';
};

const canUseContext = (context: Awaited<ReturnType<typeof buildContext>>) =>
  context.blocks.length > 0 && !context.confidence.low_confidence;

const answerabilityReason = (context: Awaited<ReturnType<typeof buildContext>>) => {
  if (context.blocks.length === 0) {
    return 'no_context_blocks';
  }
  if (context.confidence.low_confidence) {
    return context.confidence.reason || 'low_confidence';
  }
  return null;
};

const makeWarnings = (context: Awaited<ReturnType<typeof buildContext>>) => {
  const warnings: string[] = [];
  if (context.confidence.low_confidence) {
    warnings.push('low_confidence');
  }
  if (context.blocks.length === 0) {
    warnings.push('empty_context');
  }
  if (context.omitted.token_budget > 0) {
    warnings.push('token_budget_omitted_results');
  }
  if (context.omitted.source_limit > 0) {
    warnings.push('source_limit_omitted_results');
  }
  return warnings;
};

const makeMessages = (query: string, contextText: string) => [
  {
    role: 'system',
    content:
      'Use the provided Lucas Database context as grounding. If confidence is low or the context is insufficient, say that clearly. Cite sources using [Source N].'
  },
  {
    role: 'user',
    content: `Question:\n${query}\n\nLucas Database context:\n${contextText || '(no context returned)'}`
  }
];

export const retrieveForAgent = async (input: AgentRetrieveInput) => {
  const context = await buildContext(input);
  const agent = normalizeAgentName(input.agent);
  const responseFormat = normalizeResponseFormat(input.response_format);
  const usable = canUseContext(context);
  const warnings = makeWarnings(context);
  const response = {
    ok: true,
    api_version: 'agent-retrieve-v0',
    adapter: {
      name: 'lucas-agent-retrieve',
      profile: 'generic-http-json',
      requested_agent: agent,
      native_adapter_status: 'generic_contract_only'
    },
    query: context.query,
    status: usable ? 'ready' : 'low_confidence',
    answerability: {
      can_answer: usable,
      reason: answerabilityReason(context)
    },
    confidence: context.confidence,
    context: {
      text: context.context_text,
      estimated_tokens: context.estimated_tokens,
      token_budget: context.token_budget,
      source_count: context.sources.length,
      block_count: context.blocks.length
    },
    sources: context.sources.map((source) => ({
      source_index: source.source_index,
      citation_label: `[Source ${source.source_index}]`,
      source_key: source.source_key,
      source_type: source.source_type,
      source_id: source.source_id,
      title: source.title,
      path: source.path,
      chunk_ids: source.chunk_ids,
      best_score: source.best_score
    })),
    citations: context.blocks.map((block) => ({
      source_index: block.source_index,
      citation_label: `[Source ${block.source_index}]`,
      chunk_id: block.chunk_id,
      title: block.title,
      path: block.path,
      text: block.text,
      estimated_tokens: block.estimated_tokens,
      score: block.hybrid_score,
      signals: block.signals
    })),
    retrieval: context.retrieval,
    omitted: context.omitted,
    warnings,
    usage: {
      context_field: 'context.text',
      citation_rule: 'Cite evidence with citation_label such as [Source 1].',
      low_confidence_rule: 'If answerability.can_answer is false, ask for clarification or say no reliable Lucas Database match was found.'
    }
  };

  if (responseFormat === 'messages') {
    return {
      ...response,
      messages: makeMessages(context.query, context.context_text)
    };
  }

  return response;
};

export const getAgentRetrieveSpec = () => ({
  ok: true,
  api_version: 'agent-retrieve-v0',
  adapter: {
    name: 'lucas-agent-retrieve',
    profile: 'generic-http-json',
    native_adapter_status: 'generic_contract_only'
  },
  endpoint: {
    method: 'POST',
    path: '/api/agent/retrieve',
    auth: 'none in V0 read-only local API',
    content_type: 'application/json'
  },
  request_schema: {
    query: 'string, required',
    limit: 'integer, optional, 1..50, default 12',
    token_budget: 'integer, optional, 200..12000, default 1800',
    max_chunks_per_source: 'integer, optional, 1..10, default 3',
    agent: 'string, optional, caller label such as openclaw or hermes',
    response_format: 'default | messages, optional'
  },
  response_contract: {
    status: 'ready | low_confidence',
    answerability: 'can_answer boolean plus reason',
    context: 'text, estimated_tokens, token_budget, source_count, block_count',
    sources: 'source records with citation_label, title, path, source id and chunk ids',
    citations: 'chunk-level evidence blocks with citation labels and retrieval signals',
    confidence: 'hybrid retrieval confidence trace',
    warnings: 'low_confidence, empty_context, token_budget_omitted_results, source_limit_omitted_results'
  },
  example_request: {
    query: 'App Store Connect 上架 iPhone 应用需要准备什么',
    limit: 6,
    token_budget: 1800,
    max_chunks_per_source: 2,
    agent: 'openclaw',
    response_format: 'messages'
  },
  native_adapter_note:
    'OpenClaw/Hermes native plugin manifests or tool schemas are intentionally not guessed. Provide their real adapter documentation to add native manifests.'
});
