#!/usr/bin/env node

const DEFAULT_BASE_URL = 'http://127.0.0.1:8765';
const SERVER_NAME = 'lucas-mcp-retrieval';
const SERVER_VERSION = '0.1.0';

let inputBuffer = '';
let transportMode = 'auto';

const toolInputSchema = {
  type: 'object',
  properties: {
    query: {
      type: 'string',
      description: 'Question or search intent to retrieve from Lucas Database.'
    },
    limit: {
      type: 'integer',
      minimum: 1,
      maximum: 50,
      description: 'Maximum context blocks to return.'
    },
    token_budget: {
      type: 'integer',
      minimum: 200,
      maximum: 12000,
      description: 'Approximate token budget for returned context.'
    },
    max_chunks_per_source: {
      type: 'integer',
      minimum: 1,
      maximum: 10,
      description: 'Maximum chunks accepted from one source.'
    },
    response_format: {
      type: 'string',
      enum: ['default', 'messages'],
      description: 'Use messages when a system/user prompt pair is useful.'
    }
  },
  required: ['query'],
  additionalProperties: false
};

const writeMessage = (message) => {
  const body = JSON.stringify(message);
  if (transportMode === 'framed') {
    process.stdout.write(`Content-Length: ${Buffer.byteLength(body, 'utf8')}\r\n\r\n${body}`);
    return;
  }
  process.stdout.write(`${body}\n`);
};

const writeResult = (id, result) => {
  writeMessage({ jsonrpc: '2.0', id, result });
};

const writeError = (id, code, message, data) => {
  writeMessage({
    jsonrpc: '2.0',
    id,
    error: {
      code,
      message,
      ...(data === undefined ? {} : { data })
    }
  });
};

const clampInt = (value, fallback, min, max) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return fallback;
  }
  return Math.max(min, Math.min(Math.trunc(value), max));
};

const baseUrl = () => {
  const raw =
    process.env.LUCAS_AGENT_RETRIEVE_BASE_URL ||
    process.env.LUCAS_DB_API_BASE_URL ||
    DEFAULT_BASE_URL;
  return raw.replace(/\/+$/, '');
};

const token = () =>
  process.env.LUCAS_AGENT_RETRIEVE_TOKEN || process.env.LUCAS_DB_API_TOKEN || '';

const timeoutMs = () =>
  clampInt(Number(process.env.LUCAS_AGENT_RETRIEVE_TIMEOUT_MS), 60000, 1000, 300000);

const parseJsonResponse = async (response) => {
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return { raw_text: text };
  }
};

const callLucasRetrieve = async (args) => {
  const query = typeof args?.query === 'string' ? args.query.trim() : '';
  if (!query) {
    return {
      ok: false,
      error: {
        code: 'LUCAS_QUERY_REQUIRED',
        message: 'query is required'
      }
    };
  }

  const body = {
    query,
    limit: clampInt(args.limit, 6, 1, 50),
    token_budget: clampInt(args.token_budget, 1800, 200, 12000),
    max_chunks_per_source: clampInt(args.max_chunks_per_source, 2, 1, 10),
    agent: 'codex-mcp',
    response_format: args.response_format === 'messages' ? 'messages' : 'default'
  };
  const controller = new AbortController();
  const timeout = setTimeout(() => {
    controller.abort(new Error(`Lucas retrieval timed out after ${timeoutMs()}ms`));
  }, timeoutMs());

  try {
    const authToken = token();
    const response = await fetch(`${baseUrl()}/api/agent/retrieve`, {
      method: 'POST',
      signal: controller.signal,
      headers: {
        'content-type': 'application/json',
        ...(authToken ? { authorization: `Bearer ${authToken}` } : {})
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
        message: error instanceof Error ? error.message : String(error)
      }
    };
  } finally {
    clearTimeout(timeout);
  }
};

const toolResult = (payload) => ({
  content: [
    {
      type: 'text',
      text: JSON.stringify(payload, null, 2)
    }
  ],
  structuredContent: payload,
  isError: payload?.ok === false
});

const handleRequest = async (request) => {
  const { id, method, params } = request;

  if (!method || typeof method !== 'string') {
    writeError(id ?? null, -32600, 'Invalid Request');
    return;
  }

  if (id === undefined || id === null) {
    return;
  }

  switch (method) {
    case 'initialize':
      writeResult(id, {
        protocolVersion: params?.protocolVersion || '2025-06-18',
        capabilities: {
          tools: {}
        },
        serverInfo: {
          name: SERVER_NAME,
          version: SERVER_VERSION
        }
      });
      return;

    case 'tools/list':
      writeResult(id, {
        tools: [
          {
            name: 'lucas_retrieve',
            title: 'Lucas Retrieve',
            description:
              'Retrieve grounded context, sources, citations, answerability, confidence, and warnings from the local Lucas Database for questions about saved notes, cards, nodes, project history, or local knowledge.',
            inputSchema: toolInputSchema
          }
        ]
      });
      return;

    case 'tools/call': {
      if (params?.name !== 'lucas_retrieve') {
        writeError(id, -32602, `Unknown tool: ${params?.name || '(missing)'}`);
        return;
      }
      const payload = await callLucasRetrieve(params.arguments || {});
      writeResult(id, toolResult(payload));
      return;
    }

    case 'resources/list':
      writeResult(id, { resources: [] });
      return;

    case 'prompts/list':
      writeResult(id, { prompts: [] });
      return;

    case 'ping':
      writeResult(id, {});
      return;

    default:
      writeError(id, -32601, `Method not found: ${method}`);
  }
};

const handleJsonMessage = (raw) => {
  if (!raw.trim()) {
    return;
  }
  let message;
  try {
    message = JSON.parse(raw);
  } catch (error) {
    writeError(null, -32700, 'Parse error', error instanceof Error ? error.message : String(error));
    return;
  }
  handleRequest(message).catch((error) => {
    writeError(
      message?.id ?? null,
      -32603,
      'Internal error',
      error instanceof Error ? error.message : String(error)
    );
  });
};

const parseFramedMessages = () => {
  while (inputBuffer.length > 0) {
    const crlfSeparatorIndex = inputBuffer.indexOf('\r\n\r\n');
    const lfSeparatorIndex = inputBuffer.indexOf('\n\n');
    const hasCrlfSeparator = crlfSeparatorIndex >= 0;
    const separatorIndex = hasCrlfSeparator ? crlfSeparatorIndex : lfSeparatorIndex;
    const separatorLength = hasCrlfSeparator ? 4 : 2;
    if (separatorIndex < 0) {
      return;
    }

    const header = inputBuffer.slice(0, separatorIndex);
    const match = /content-length:\s*(\d+)/i.exec(header);
    if (!match) {
      writeError(null, -32600, 'Invalid MCP frame header');
      inputBuffer = '';
      return;
    }

    const contentLength = Number.parseInt(match[1], 10);
    const bodyStart = separatorIndex + separatorLength;
    const bodyEnd = bodyStart + contentLength;
    if (inputBuffer.length < bodyEnd) {
      return;
    }

    const body = inputBuffer.slice(bodyStart, bodyEnd);
    inputBuffer = inputBuffer.slice(bodyEnd);
    handleJsonMessage(body);
  }
};

const parseLineMessages = () => {
  let newlineIndex = inputBuffer.indexOf('\n');
  while (newlineIndex >= 0) {
    const line = inputBuffer.slice(0, newlineIndex);
    inputBuffer = inputBuffer.slice(newlineIndex + 1);
    handleJsonMessage(line);
    newlineIndex = inputBuffer.indexOf('\n');
  }
};

const parseInputBuffer = () => {
  if (transportMode === 'auto') {
    const trimmedStart = inputBuffer.trimStart();
    if (/^content-length:/i.test(trimmedStart)) {
      transportMode = 'framed';
    } else if (trimmedStart.startsWith('{')) {
      transportMode = 'line';
    } else {
      return;
    }
  }

  if (transportMode === 'framed') {
    parseFramedMessages();
  } else {
    parseLineMessages();
  }
};

process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  inputBuffer += chunk;
  parseInputBuffer();
});

process.stdin.on('end', () => {
  if (transportMode !== 'framed' && inputBuffer.trim()) {
    handleJsonMessage(inputBuffer);
  }
});
