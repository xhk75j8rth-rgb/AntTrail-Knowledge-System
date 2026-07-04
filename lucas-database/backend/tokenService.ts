import { createHash, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { ApiError } from './errors';
import { getDb, withTransaction } from './db';

export type ApiTokenSource = 'env' | 'local_config' | 'database';

export interface ApiTokenStatus {
  ok: true;
  apiBaseUrl: string;
  configured: boolean;
  source: ApiTokenSource;
  tokenPreview: string | null;
  copyable: boolean;
}

export interface ResetApiTokenResult {
  ok: true;
  apiBaseUrl: string;
  token: string;
  tokenPreview: string;
}

export interface VerifyApiTokenResult {
  ok: true;
  valid: boolean;
  tokenPreview: string | null;
  source: ApiTokenSource;
}

export interface ApiAuthHealth {
  configured: boolean;
  source: ApiTokenSource;
}

export interface TokenCheckResult {
  valid: boolean;
  source: ApiTokenSource | 'invalid';
  tokenId?: string;
}

const DEFAULT_DEV_TOKEN = 'lucas-local-dev-token';
const TOKEN_PREFIX = 'lucas_db_';
const DEFAULT_API_PORT = Number(process.env.LUCAS_DB_PORT || 8765);

const hashToken = (token: string) => createHash('sha256').update(token, 'utf8').digest('hex');

const getApiBaseUrlInternal = () =>
  process.env.LUCAS_DB_API_BASE_URL || `http://localhost:${DEFAULT_API_PORT}`;

const previewToken = (token: string) => {
  if (token.length <= 12) {
    return `${token.slice(0, 4)}****`;
  }

  return `${token.slice(0, 10)}****${token.slice(-4)}`;
};

const safeCompare = (left: string, right: string) => {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
};

const matchesToken = (received: string, expectedToken: string) =>
  safeCompare(hashToken(received), hashToken(expectedToken));

const matchesHash = (received: string, expectedHash: string) =>
  safeCompare(hashToken(received), expectedHash);

const getLatestDatabaseTokenRow = () => {
  const db = getDb();
  return db
    .prepare(
      `
        SELECT id, token_hash, token_preview, created_at
        FROM api_tokens
        ORDER BY created_at DESC
        LIMIT 1
      `
    )
    .get() as
    | {
        id: string;
        token_hash: string;
        token_preview: string;
        created_at: string;
      }
    | undefined;
};

const storeDatabaseToken = (token: string) => {
  const db = getDb();
  const now = new Date().toISOString();
  const tokenPreview = previewToken(token);

  withTransaction(() => {
    db.prepare('DELETE FROM api_tokens').run();
    db.prepare(
      `
        INSERT INTO api_tokens (
          id,
          name,
          token_hash,
          token_preview,
          scopes,
          created_at,
          last_used_at
        )
        VALUES (
          @id,
          @name,
          @token_hash,
          @token_preview,
          @scopes,
          @created_at,
          @last_used_at
        )
      `
    ).run({
      id: `token_${randomUUID()}`,
      name: 'Local API Token',
      token_hash: hashToken(token),
      token_preview: tokenPreview,
      scopes: JSON.stringify(['read', 'write']),
      created_at: now,
      last_used_at: null
    });
  });

  return { tokenPreview };
};

export const generateApiToken = () => `${TOKEN_PREFIX}${randomBytes(24).toString('hex')}`;

export const getApiBaseUrl = () => getApiBaseUrlInternal();

export const getApiTokenStatus = (): ApiTokenStatus => {
  const envToken = process.env.LUCAS_DB_API_TOKEN;
  if (envToken) {
    return {
      ok: true,
      apiBaseUrl: getApiBaseUrlInternal(),
      configured: true,
      source: 'env',
      tokenPreview: previewToken(envToken),
      copyable: true
    };
  }

  const latest = getLatestDatabaseTokenRow();
  if (latest) {
    return {
      ok: true,
      apiBaseUrl: getApiBaseUrlInternal(),
      configured: true,
      source: 'database',
      tokenPreview: latest.token_preview || null,
      copyable: false
    };
  }

  return {
    ok: true,
    apiBaseUrl: getApiBaseUrlInternal(),
    configured: false,
    source: 'local_config',
    tokenPreview: null,
    copyable: false
  };
};

export const resetApiToken = (): ResetApiTokenResult => {
  if (process.env.LUCAS_DB_API_TOKEN) {
    throw new ApiError(
      'TOKEN_ENV_LOCKED',
      'API token is managed by LUCAS_DB_API_TOKEN and cannot be reset in V0',
      409
    );
  }

  const token = generateApiToken();
  const { tokenPreview } = storeDatabaseToken(token);
  return {
    ok: true,
    apiBaseUrl: getApiBaseUrlInternal(),
    token,
    tokenPreview
  };
};

export const checkBearerToken = (received: string): TokenCheckResult => {
  const envToken = process.env.LUCAS_DB_API_TOKEN;
  if (envToken) {
    return matchesToken(received, envToken)
      ? { valid: true, source: 'env' }
      : { valid: false, source: 'invalid' };
  }

  const latest = getLatestDatabaseTokenRow();
  if (latest) {
    return matchesHash(received, latest.token_hash)
      ? { valid: true, source: 'database', tokenId: latest.id }
      : { valid: false, source: 'invalid' };
  }

  return matchesToken(received, DEFAULT_DEV_TOKEN)
    ? { valid: true, source: 'local_config' }
    : { valid: false, source: 'invalid' };
};

export const verifyApiToken = (received: unknown): VerifyApiTokenResult => {
  const token = typeof received === 'string' ? received.trim() : '';
  if (!token) {
    throw new ApiError('TOKEN_MISSING', 'Authorization bearer token is required', 401);
  }

  const result = checkBearerToken(token);
  const activeStatus = getApiTokenStatus();

  if (result.valid && result.tokenId) {
    touchDatabaseTokenUsage(result.tokenId);
  }

  return {
    ok: true,
    valid: result.valid,
    tokenPreview: previewToken(token),
    source: result.valid && result.source !== 'invalid' ? result.source : activeStatus.source
  };
};

export const getApiAuthHealth = (): ApiAuthHealth => {
  const status = getApiTokenStatus();
  return {
    configured: status.configured,
    source: status.source
  };
};

export const touchDatabaseTokenUsage = (tokenId: string) => {
  const db = getDb();
  db.prepare('UPDATE api_tokens SET last_used_at = ? WHERE id = ?').run(
    new Date().toISOString(),
    tokenId
  );
};

export const getDefaultDevToken = () => DEFAULT_DEV_TOKEN;
