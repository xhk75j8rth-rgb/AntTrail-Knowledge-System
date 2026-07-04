import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import cors from 'cors';
import express from 'express';
import multer from 'multer';
import { ATTACHMENTS_DIR, DB_PATH, getDb } from './db';
import { requireWriteAuth } from './auth';
import {
  createAttachment,
  getAttachmentFilePath,
  getAttachmentOrThrow,
  getAttachmentUrl,
  softDeleteAttachment
} from './attachmentsRepo';
import { ApiError, sendError } from './errors';
import { createRelation, deleteRelation, getGraphData, rebuildGraphForCard } from './graphService';
import {
  appendToNode,
  buildTree,
  createChildNode,
  createNode,
  getNodeDetail,
  softDeleteNode,
  updateNode
} from './nodesRepo';
import { writeByPath } from './pathService';
import { searchNodes } from './searchService';
import { getApiAuthHealth, getApiTokenStatus, resetApiToken, verifyApiToken } from './tokenService';
import { getCardDetail, getCardsByNode, ingestCard } from './cardsRepo';
import { getAgentRetrieveSpec, retrieveForAgent } from './indexing/agentRetrieveService';
import { getChunkOrThrow, listChunksByTarget } from './indexing/chunkRepo';
import { buildContext } from './indexing/contextBuilderService';
import { listEmbeddingJobs } from './indexing/embeddingJobRepo';
import { getActiveVectorByChunkId } from './indexing/embeddingVectorRepo';
import { getEmbeddingProviderDescriptor } from './indexing/embeddingProviderFactory';
import { rebuildAllIndexes, rebuildCardIndex, rebuildNodeIndex } from './indexing/indexingService';
import {
  searchHybridRetrieval,
  searchKeywordRetrieval,
  searchVectorRetrieval
} from './indexing/retrievalService';
import { getVectorStoreDescriptor } from './indexing/vectorStoreFactory';

const app = express();
const port = Number(process.env.LUCAS_DB_PORT || 8765);
const uploadTempDir = path.join(ATTACHMENTS_DIR, '.tmp');
fs.mkdirSync(uploadTempDir, { recursive: true });
const upload = multer({
  dest: uploadTempDir,
  limits: {
    fileSize: Number(process.env.LUCAS_DB_MAX_UPLOAD_BYTES || 1024 * 1024 * 500)
  }
});

app.use(cors());
app.use(express.json({ limit: '20mb' }));

app.use((req, res, next) => {
  const requestId = req.header('x-request-id') || `req_${randomUUID()}`;
  res.locals.requestId = requestId;
  res.setHeader('X-Request-Id', requestId);
  next();
});

const bearerTokenFromRequest = (req: express.Request) => {
  const header = req.header('authorization') || '';
  const match = header.match(/^Bearer\s+(.+)$/i);
  return match?.[1] || null;
};

const parseMetadataJson = (value: string | null) => {
  if (!value) {
    return {};
  }

  try {
    const parsed = JSON.parse(value);
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
};

const isAutoIndexOnCardIngestEnabled = () =>
  String(process.env.LUCAS_AUTO_INDEX_ON_CARD_INGEST || 'true').toLowerCase() !== 'false';

let cardAutoIndexQueue = Promise.resolve();

const enqueueCardAutoIndex = (cardId: string, requestId: string) => {
  const queuedAt = new Date().toISOString();
  cardAutoIndexQueue = cardAutoIndexQueue
    .catch(() => undefined)
    .then(async () => {
      try {
        const result = await rebuildCardIndex(cardId);
        console.info(
          `[indexing:auto-card] request_id=${requestId} card_id=${cardId} job_id=${result.job_id} chunks=${result.chunk_count}`
        );
      } catch (error) {
        console.error(
          `[indexing:auto-card] request_id=${requestId} card_id=${cardId} failed`,
          error
        );
      }
    });

  return {
    started: true,
    mode: 'background',
    target_type: 'card',
    target_id: cardId,
    queued_at: queuedAt
  };
};

const serializeAttachment = (attachment: ReturnType<typeof getAttachmentOrThrow>) => ({
  id: attachment.id,
  node_id: attachment.node_id,
  original_name: attachment.original_name,
  mime_type: attachment.mime_type,
  size_bytes: attachment.size_bytes,
  kind: attachment.kind,
  created_at: attachment.created_at,
  deleted_at: attachment.deleted_at,
  url: getAttachmentUrl(attachment.id)
});

const sendAttachmentFile = (attachmentId: string, res: express.Response) => {
  const attachment = getAttachmentOrThrow(attachmentId);
  const filePath = getAttachmentFilePath(attachment);
  if (!fs.existsSync(filePath)) {
    throw new ApiError('ATTACHMENT_FILE_MISSING', 'Attachment file is missing', 404);
  }

  res.type(attachment.mime_type);
  res.setHeader(
    'Content-Disposition',
    `inline; filename*=UTF-8''${encodeURIComponent(attachment.original_name)}`
  );
  res.sendFile(filePath);
};

app.get('/api/health', (_req, res, next) => {
  try {
    getDb();
    res.json({
      ok: true,
      name: 'AntTrail Database',
      version: '0.1.0',
      database: {
        ok: true,
        path: DB_PATH
      },
      auth: getApiAuthHealth(),
      indexing: {
        embedding_provider: getEmbeddingProviderDescriptor(),
        vector_store: getVectorStoreDescriptor()
      }
    });
  } catch (error) {
    next(error);
  }
});

app.get('/api/tree', (_req, res, next) => {
  try {
    res.json(buildTree());
  } catch (error) {
    next(error);
  }
});

app.get('/api/settings/api-token', (_req, res, next) => {
  try {
    res.json(getApiTokenStatus());
  } catch (error) {
    next(error);
  }
});

app.post('/api/settings/api-token/reset', (_req, res, next) => {
  try {
    res.json(resetApiToken());
  } catch (error) {
    next(error);
  }
});

app.post('/api/settings/api-token/verify', (req, res, next) => {
  try {
    res.json(verifyApiToken(bearerTokenFromRequest(req)));
  } catch (error) {
    next(error);
  }
});

app.post('/api/nodes', requireWriteAuth, (req, res, next) => {
  try {
    const node = createNode({
      parent_id: req.body.parent_id ?? null,
      title: req.body.title,
      content: req.body.content || '',
      type: req.body.type || 'page',
      actor: req.body.actor || 'user'
    });
    res.status(201).json(node);
  } catch (error) {
    next(error);
  }
});

app.post('/api/nodes/:id/children', requireWriteAuth, (req, res, next) => {
  try {
    const node = createChildNode(String(req.params.id), {
      title: req.body.title,
      content: req.body.content || '',
      type: req.body.type || 'page',
      actor: req.body.actor || 'user'
    });
    res.status(201).json(node);
  } catch (error) {
    next(error);
  }
});

app.get('/api/nodes/:id', (req, res, next) => {
  try {
    const detail = getNodeDetail(String(req.params.id));
    res.json({
      ...detail,
      attachments: detail.attachments.map(serializeAttachment)
    });
  } catch (error) {
    next(error);
  }
});

app.post(
  '/api/nodes/:id/attachments',
  requireWriteAuth,
  upload.single('file'),
  (req, res, next) => {
    try {
      if (!req.file) {
        throw new ApiError('ATTACHMENT_REQUIRED', 'file is required', 400);
      }

      const attachment = createAttachment({
        nodeId: String(req.params.id),
        originalName: req.file.originalname,
        mimeType: req.file.mimetype,
        sizeBytes: req.file.size,
        tempPath: req.file.path,
        actor: typeof req.body?.actor === 'string' ? req.body.actor : 'user'
      });

      res.status(201).json({
        ok: true,
        attachment: serializeAttachment(attachment)
      });
    } catch (error) {
      if (req.file?.path) {
        fs.rmSync(req.file.path, { force: true });
      }
      next(error);
    }
  }
);

app.get('/api/attachments/:id/file', (req, res, next) => {
  try {
    sendAttachmentFile(String(req.params.id), res);
  } catch (error) {
    next(error);
  }
});

app.get('/api/assets/:id', (req, res, next) => {
  try {
    sendAttachmentFile(String(req.params.id), res);
  } catch (error) {
    next(error);
  }
});

app.delete('/api/attachments/:id', requireWriteAuth, (req, res, next) => {
  try {
    res.json(softDeleteAttachment(String(req.params.id), req.body?.actor || 'user'));
  } catch (error) {
    next(error);
  }
});

app.patch('/api/nodes/:id', requireWriteAuth, (req, res, next) => {
  try {
    res.json(
      updateNode(String(req.params.id), {
        title: req.body.title,
        content: req.body.content,
        actor: req.body.actor || 'user'
      })
    );
  } catch (error) {
    next(error);
  }
});

app.post('/api/nodes/:id/append', requireWriteAuth, (req, res, next) => {
  try {
    res.json(appendToNode(String(req.params.id), req.body.content, req.body.actor || 'user'));
  } catch (error) {
    next(error);
  }
});

app.post('/api/write-by-path', requireWriteAuth, (req, res, next) => {
  try {
    res.json(
      writeByPath({
        path: req.body.path,
        content: req.body.content,
        mode: req.body.mode,
        actor: req.body.actor || 'agent'
      })
    );
  } catch (error) {
    next(error);
  }
});

app.post('/api/cards/ingest', requireWriteAuth, (req, res, next) => {
  try {
    const receipt = ingestCard({
      card: req.body.card,
      source_material: req.body.source_material,
      quality_gate: req.body.quality_gate,
      rendered_views: req.body.rendered_views,
      relations: req.body.relations,
      target_path: req.body.target_path,
      idempotency_key: req.header('Idempotency-Key') || req.body.idempotency_key,
      actor: req.body.actor || 'agent'
    });
    if (receipt.data.created || receipt.data.updated) {
      rebuildGraphForCard(receipt.data.card_id);
    }
    const indexing =
      receipt.data.created || receipt.data.updated
        ? isAutoIndexOnCardIngestEnabled()
          ? {
              auto_rebuild: enqueueCardAutoIndex(
                receipt.data.card_id,
                res.locals.requestId
              )
            }
          : {
              auto_rebuild: {
                started: false,
                reason: 'disabled'
              }
            }
        : {
            auto_rebuild: {
              started: false,
              reason: 'unchanged'
            }
          };
    res.status(receipt.data.created ? 201 : 200).json({
      ...receipt,
      data: {
        ...receipt.data,
        indexing
      }
    });
  } catch (error) {
    next(error);
  }
});

app.get('/api/cards/:id', (req, res, next) => {
  try {
    res.json(getCardDetail(String(req.params.id)));
  } catch (error) {
    next(error);
  }
});

app.get('/api/nodes/:id/cards', (req, res, next) => {
  try {
    res.json(getCardsByNode(String(req.params.id)));
  } catch (error) {
    next(error);
  }
});

app.get('/api/search', (req, res, next) => {
  try {
    res.json(searchNodes(req.query.q));
  } catch (error) {
    next(error);
  }
});

app.get('/api/graph', (req, res, next) => {
  try {
    res.json(
      getGraphData({
        nodeId: req.query.node_id,
        depth: req.query.depth,
        scope: req.query.scope
      })
    );
  } catch (error) {
    next(error);
  }
});

app.post('/api/indexing/rebuild-node/:id', requireWriteAuth, (req, res, next) => {
  rebuildNodeIndex(String(req.params.id)).then((result) => res.json(result)).catch(next);
});

app.post('/api/indexing/rebuild-card/:id', requireWriteAuth, (req, res, next) => {
  rebuildCardIndex(String(req.params.id)).then((result) => res.json(result)).catch(next);
});

app.post('/api/indexing/rebuild-all', requireWriteAuth, (req, res, next) => {
  rebuildAllIndexes({
    targetTypes: req.body?.target_types,
    limit: req.body?.limit
  })
    .then((result) => res.json(result))
    .catch(next);
});

app.post('/api/retrieval/vector', (req, res, next) => {
  searchVectorRetrieval(req.body).then((result) => res.json(result)).catch(next);
});

app.post('/api/retrieval/keyword', (req, res, next) => {
  searchKeywordRetrieval(req.body).then((result) => res.json(result)).catch(next);
});

app.post('/api/retrieval/hybrid', (req, res, next) => {
  searchHybridRetrieval(req.body).then((result) => res.json(result)).catch(next);
});

app.post('/api/context/build', (req, res, next) => {
  buildContext(req.body).then((result) => res.json(result)).catch(next);
});

app.get('/api/agent/retrieve/spec', (_req, res, next) => {
  try {
    res.json(getAgentRetrieveSpec());
  } catch (error) {
    next(error);
  }
});

app.post('/api/agent/retrieve', (req, res, next) => {
  retrieveForAgent(req.body).then((result) => res.json(result)).catch(next);
});

app.get('/api/indexing/provider', (_req, res, next) => {
  try {
    res.json({
      ok: true,
      embedding_provider: getEmbeddingProviderDescriptor(),
      vector_store: getVectorStoreDescriptor()
    });
  } catch (error) {
    next(error);
  }
});

app.get('/api/indexing/jobs', (req, res, next) => {
  try {
    res.json({
      ok: true,
      jobs: listEmbeddingJobs(req.query)
    });
  } catch (error) {
    next(error);
  }
});

app.get('/api/indexing/chunks', (req, res, next) => {
  try {
    const targetType = typeof req.query.target_type === 'string' ? req.query.target_type : '';
    const targetId = typeof req.query.target_id === 'string' ? req.query.target_id : '';
    if (!targetType || !targetId) {
      throw new ApiError('INDEXING_TARGET_REQUIRED', 'target_type and target_id are required', 400);
    }

    res.json({
      ok: true,
      chunks: listChunksByTarget(targetType, targetId).map((chunk) => ({
        id: chunk.id,
        target_type: chunk.target_type,
        target_id: chunk.target_id,
        source_table: chunk.source_table,
        chunk_type: chunk.chunk_type,
        chunk_text: chunk.chunk_text,
        chunk_index: chunk.chunk_index,
        text_hash: chunk.text_hash,
        token_count: chunk.token_count,
        metadata: parseMetadataJson(chunk.metadata_json),
        created_at: chunk.created_at,
        updated_at: chunk.updated_at
      }))
    });
  } catch (error) {
    next(error);
  }
});

app.get('/api/indexing/chunks/:id/vector', (req, res, next) => {
  try {
    const chunk = getChunkOrThrow(String(req.params.id));
    const vector = getActiveVectorByChunkId(chunk.id);
    res.json({
      ok: true,
      chunk_id: chunk.id,
      vector_store: vector.vector_store,
      vector_id: vector.vector_id,
      embedding_model: vector.embedding_model,
      embedding_dim: vector.embedding_dim,
      text_hash: vector.text_hash,
      metadata: parseMetadataJson(vector.metadata_json)
    });
  } catch (error) {
    next(error);
  }
});

app.post('/api/relations', requireWriteAuth, (req, res, next) => {
  try {
    res.status(201).json(createRelation(req.body));
  } catch (error) {
    next(error);
  }
});

app.delete('/api/relations/:id', requireWriteAuth, (req, res, next) => {
  try {
    res.json(deleteRelation(String(req.params.id)));
  } catch (error) {
    next(error);
  }
});

app.post('/api/cards/:id/rebuild-graph', requireWriteAuth, (req, res, next) => {
  try {
    res.json(rebuildGraphForCard(String(req.params.id)));
  } catch (error) {
    next(error);
  }
});

app.delete('/api/nodes/:id', requireWriteAuth, (req, res, next) => {
  try {
    res.json({
      ok: true,
      ...softDeleteNode(String(req.params.id), req.body?.actor || 'user')
    });
  } catch (error) {
    next(error);
  }
});

app.use((req, _res, next) => {
  next(new ApiError('NOT_FOUND', `Route not found: ${req.method} ${req.path}`, 404));
});

app.use((error: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  sendError(res, error, String(res.locals.requestId || `req_${randomUUID()}`));
});

app.listen(port, '127.0.0.1', () => {
  getDb();
  console.log(`AntTrail Database API listening at http://127.0.0.1:${port}`);
  console.log(`SQLite database: ${DB_PATH}`);
});
