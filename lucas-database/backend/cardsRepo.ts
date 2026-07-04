import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import { ApiError } from './errors';
import { getDb, withTransaction } from './db';
import {
  copyLocalFileToAttachment,
  getAssetUrl,
  getAttachmentFilePath
} from './attachmentsRepo';
import { findChildByTitle, updateNode, createNode, getNodeOrThrow } from './nodesRepo';
import { recordNodeEvent } from './nodeEventsRepo';
import type {
  CardCommentSignalRow,
  CardDetail,
  CardItemRow,
  CardKnowledgeBlockRow,
  CardQualityGateRow,
  CardRenderedViewRow,
  CardRow,
  CardSourceMaterialRow,
  NodeRelationRow
} from './cardTypes';
import type { NodeAttachment, NodeRow } from './types';

type JsonRecord = Record<string, unknown>;

interface IngestInput {
  card: unknown;
  source_material?: unknown;
  quality_gate?: unknown;
  rendered_views?: unknown;
  relations?: unknown;
  target_path: unknown;
  idempotency_key?: unknown;
  actor?: unknown;
}

interface IngestReceipt {
  ok: true;
  data: {
    card_id: string;
    node_id: string;
    path: string;
    created: boolean;
    updated: boolean;
  };
}

interface PreparedRelation {
  from_node_id: string | null;
  to_node_id: string | null;
  from_card_id: string | null;
  to_card_id: string | null;
  relation_type: string;
  label: string | null;
  source: string;
  confidence: number;
  metadata_json: string | null;
}

const isRecord = (value: unknown): value is JsonRecord =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const asString = (value: unknown): string | null =>
  typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;

const asLooseString = (value: unknown): string | null =>
  typeof value === 'string' ? value : value === undefined || value === null ? null : String(value);

const asKeyString = (value: unknown): string | null => {
  const raw =
    typeof value === 'string'
      ? value
      : value === undefined || value === null
        ? null
        : String(value);
  const trimmed = raw?.trim();
  return trimmed ? trimmed.slice(0, 512) : null;
};

const jsonOrNull = (value: unknown) =>
  value === undefined || value === null ? null : JSON.stringify(value);

const normalizePath = (value: unknown) => {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new ApiError('PATH_REQUIRED', 'target_path is required', 400);
  }

  const segments = value
    .split('/')
    .map((segment) => segment.trim())
    .filter(Boolean);
  if (segments.length === 0) {
    throw new ApiError('PATH_REQUIRED', 'target_path is required', 400);
  }

  return {
    segments,
    path: `/${segments.join('/')}`
  };
};

const requireCard = (value: unknown): JsonRecord => {
  if (!isRecord(value)) {
    throw new ApiError('SCHEMA_INVALID', 'card must be a ComposedCardV1 object', 400);
  }

  const schemaName = asString(value.schema_name);
  const schemaVersion = asString(value.schema_version);
  if (schemaName !== 'ComposedCardV1') {
    throw new ApiError(
      'SCHEMA_INVALID',
      'card.schema_name must be "ComposedCardV1"',
      400
    );
  }
  if (!schemaVersion) {
    throw new ApiError('SCHEMA_INVALID', 'card.schema_version is required', 400);
  }

  return value;
};

const requireQualityGatePassed = (value: unknown): JsonRecord => {
  if (!isRecord(value) || value.passed !== true) {
    throw new ApiError(
      'QUALITY_GATE_FAILED',
      'quality_gate.passed must be true before ingest',
      422
    );
  }

  return value;
};

const getArray = (record: JsonRecord, key: string): unknown[] =>
  Array.isArray(record[key]) ? (record[key] as unknown[]) : [];

const valueToContent = (value: unknown): string | null => {
  if (typeof value === 'string') {
    return value.trim().length > 0 ? value.trim() : null;
  }
  if (isRecord(value)) {
    const direct =
      asString(value.content) ||
      asString(value.text) ||
      asString(value.title) ||
      asString(value.name) ||
      asString(value.value);
    return direct || JSON.stringify(value);
  }
  if (value === undefined || value === null) {
    return null;
  }
  return String(value);
};

const makeDefaultMarkdown = (card: JsonRecord) => {
  const title = asString(card.display_title) || asString(card.source_title) || '未命名卡片';
  const summary = asLooseString(card.one_sentence_summary);
  const corePoints = getArray(card, 'core_points')
    .map(valueToContent)
    .filter((item): item is string => Boolean(item));
  const tags = getArray(card, 'tags')
    .map(valueToContent)
    .filter((item): item is string => Boolean(item));
  const blocks = getArray(card, 'knowledge_blocks').filter(isRecord);

  const lines = [`# ${title}`, ''];
  if (summary) {
    lines.push(summary, '');
  }
  if (corePoints.length > 0) {
    lines.push('## 核心观点', ...corePoints.map((point) => `- ${point}`), '');
  }
  if (blocks.length > 0) {
    lines.push('## 知识块');
    blocks.forEach((block) => {
      const concept = asString(block.concept) || '未命名概念';
      const explanation = asLooseString(block.explanation);
      lines.push(`### ${concept}`);
      if (explanation) {
        lines.push(explanation);
      }
      lines.push('');
    });
  }
  if (tags.length > 0) {
    lines.push(`标签：${tags.map((tag) => `#${tag}`).join(' ')}`, '');
  }

  return lines.join('\n').trimEnd() + '\n';
};

const makePlainText = (card: JsonRecord, markdown: string) => {
  const parts = [
    asLooseString(card.display_title),
    asLooseString(card.one_sentence_summary),
    asLooseString(card.original_summary),
    markdown.replace(/[#>*_`-]/g, ' ')
  ].filter(Boolean);
  return parts.join('\n').trim();
};

const ensurePathNode = (segments: string[], content: string, actor: string) => {
  let parentId: string | null = null;
  let current: NodeRow | undefined;

  segments.forEach((segment, index) => {
    const existing = findChildByTitle(parentId, segment);
    if (existing) {
      current = existing;
      parentId = existing.id;
      if (index === segments.length - 1) {
        updateNode(existing.id, { title: segment, content, actor });
      }
      return;
    }

    current = createNode({
      parent_id: parentId,
      title: segment,
      content: index === segments.length - 1 ? content : '',
      actor
    });
    parentId = current.id;
  });

  if (!current) {
    throw new ApiError('PATH_REQUIRED', 'target_path is required', 400);
  }

  return current;
};

const resolveIdempotencyKey = (input: IngestInput): string | null => {
  const explicitKey = asKeyString(input.idempotency_key);
  if (explicitKey) {
    return `idempotency:${explicitKey}`;
  }

  if (!isRecord(input.source_material)) {
    return null;
  }

  const metadata = isRecord(input.source_material.metadata)
    ? input.source_material.metadata
    : null;
  const jobId = metadata ? asKeyString(metadata.job_id) : null;
  return jobId ? `source_job:${jobId}` : null;
};

const findCardByIdempotencyKey = (idempotencyKey: string): CardRow | undefined => {
  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM cards
        WHERE idempotency_key = ?
          AND deleted_at IS NULL
        LIMIT 1
      `
    )
    .get(idempotencyKey) as CardRow | undefined;
};

const buildNodePath = (nodeId: string) => {
  const db = getDb();
  const segments: string[] = [];
  let currentId: string | null = nodeId;

  while (currentId) {
    const row = db
      .prepare('SELECT id, parent_id, title FROM nodes WHERE id = ?')
      .get(currentId) as Pick<NodeRow, 'id' | 'parent_id' | 'title'> | undefined;
    if (!row) {
      break;
    }
    segments.unshift(row.title);
    currentId = row.parent_id;
  }

  return segments.length > 0 ? `/${segments.join('/')}` : '';
};

const makeIngestReceipt = (input: {
  cardId: string;
  nodeId: string;
  path: string;
  created: boolean;
  updated?: boolean;
}): IngestReceipt => ({
  ok: true,
  data: {
    card_id: input.cardId,
    node_id: input.nodeId,
    path: input.path,
    created: input.created,
    updated: input.updated ?? false
  }
});

const insertCardEvent = (input: {
  cardId: string;
  nodeId: string;
  actor: string;
  action: string;
  afterSnapshot?: unknown;
}) => {
  const db = getDb();
  db.prepare(
    `
      INSERT INTO card_events (
        id,
        card_id,
        node_id,
        actor,
        action,
        before_snapshot,
        after_snapshot,
        created_at
      )
      VALUES (
        @id,
        @card_id,
        @node_id,
        @actor,
        @action,
        @before_snapshot,
        @after_snapshot,
        @created_at
      )
    `
  ).run({
    id: `card_event_${randomUUID()}`,
    card_id: input.cardId,
    node_id: input.nodeId,
    actor: input.actor,
    action: input.action,
    before_snapshot: null,
    after_snapshot:
      input.afterSnapshot === undefined ? null : JSON.stringify(input.afterSnapshot),
    created_at: new Date().toISOString()
  });
};

const insertKnowledgeBlocks = (cardId: string, card: JsonRecord, createdAt: string) => {
  const db = getDb();
  getArray(card, 'knowledge_blocks')
    .filter(isRecord)
    .forEach((block, index) => {
      const concept = asString(block.concept);
      if (!concept) {
        return;
      }
      db.prepare(
        `
          INSERT INTO card_knowledge_blocks (
            id,
            card_id,
            concept,
            explanation,
            evidence,
            reusable_value,
            sort_order,
            created_at
          )
          VALUES (
            @id,
            @card_id,
            @concept,
            @explanation,
            @evidence,
            @reusable_value,
            @sort_order,
            @created_at
          )
        `
      ).run({
        id: `kb_${randomUUID()}`,
        card_id: cardId,
        concept,
        explanation: asLooseString(block.explanation),
        evidence: asLooseString(block.evidence),
        reusable_value: asLooseString(block.reusable_value),
        sort_order: index,
        created_at: createdAt
      });
    });
};

const itemFieldMap: Array<[string, string]> = [
  ['core_points', 'core_point'],
  ['methodology', 'methodology'],
  ['application_suggestions', 'application_suggestion'],
  ['application_suggestion', 'application_suggestion'],
  ['follow_up_actions', 'follow_up_action'],
  ['follow_up_action', 'follow_up_action'],
  ['reusable_values', 'reusable_value'],
  ['reusable_value', 'reusable_value'],
  ['risks', 'risk'],
  ['risk', 'risk'],
  ['evidence_quotes', 'evidence_quote'],
  ['evidence_quote', 'evidence_quote'],
  ['tags', 'tag']
];

const insertItems = (cardId: string, card: JsonRecord, createdAt: string) => {
  const db = getDb();
  itemFieldMap.forEach(([field, itemType]) => {
    getArray(card, field).forEach((value, index) => {
      const content = valueToContent(value);
      if (!content) {
        return;
      }
      db.prepare(
        `
          INSERT INTO card_items (
            id,
            card_id,
            item_type,
            content,
            sort_order,
            created_at
          )
          VALUES (
            @id,
            @card_id,
            @item_type,
            @content,
            @sort_order,
            @created_at
          )
        `
      ).run({
        id: `item_${randomUUID()}`,
        card_id: cardId,
        item_type: itemType,
        content,
        sort_order: index,
        created_at: createdAt
      });
    });
  });
};

const insertCommentSignals = (cardId: string, card: JsonRecord, createdAt: string) => {
  const signals = isRecord(card.comment_signals)
    ? card.comment_signals
    : isRecord(card.comment_signal)
      ? card.comment_signal
      : null;
  if (!signals) {
    return;
  }

  const db = getDb();
  db.prepare(
    `
      INSERT INTO card_comment_signals (
        id,
        card_id,
        comments_hash,
        demand_or_resource_requests,
        doubts_or_objections,
        implementation_barriers,
        resonance_or_agreement,
        incremental_value,
        comments_job_id,
        comments_video_id,
        created_at
      )
      VALUES (
        @id,
        @card_id,
        @comments_hash,
        @demand_or_resource_requests,
        @doubts_or_objections,
        @implementation_barriers,
        @resonance_or_agreement,
        @incremental_value,
        @comments_job_id,
        @comments_video_id,
        @created_at
      )
    `
  ).run({
    id: `comment_signal_${randomUUID()}`,
    card_id: cardId,
    comments_hash: asLooseString(signals.comments_hash),
    demand_or_resource_requests: jsonOrNull(signals.demand_or_resource_requests),
    doubts_or_objections: jsonOrNull(signals.doubts_or_objections),
    implementation_barriers: jsonOrNull(signals.implementation_barriers),
    resonance_or_agreement: jsonOrNull(signals.resonance_or_agreement),
    incremental_value: asLooseString(signals.incremental_value),
    comments_job_id: asLooseString(signals.comments_job_id),
    comments_video_id: asLooseString(signals.comments_video_id),
    created_at: createdAt
  });
};

const insertSourceMaterial = (
  cardId: string,
  sourceMaterial: unknown,
  card: JsonRecord,
  createdAt: string
) => {
  if (!isRecord(sourceMaterial)) {
    return;
  }

  const db = getDb();
  db.prepare(
    `
      INSERT INTO card_source_materials (
        id,
        card_id,
        source_url,
        source_title,
        source_type,
        raw_text,
        transcript,
        ocr_text,
        comments_json,
        metadata_json,
        created_at
      )
      VALUES (
        @id,
        @card_id,
        @source_url,
        @source_title,
        @source_type,
        @raw_text,
        @transcript,
        @ocr_text,
        @comments_json,
        @metadata_json,
        @created_at
      )
    `
  ).run({
    id: `source_${randomUUID()}`,
    card_id: cardId,
    source_url: asLooseString(sourceMaterial.source_url),
    source_title:
      asLooseString(sourceMaterial.source_title) ||
      asLooseString(sourceMaterial.raw_title) ||
      asLooseString(card.source_title),
    source_type: asLooseString(sourceMaterial.source_type),
    raw_text: asLooseString(sourceMaterial.raw_text),
    transcript: asLooseString(sourceMaterial.transcript),
    ocr_text: asLooseString(sourceMaterial.ocr_text),
    comments_json: jsonOrNull(sourceMaterial.comments),
    metadata_json: jsonOrNull(buildSourceMaterialMetadata(sourceMaterial)),
    created_at: createdAt
  });
};

const insertQualityGate = (
  cardId: string,
  qualityGate: unknown,
  card: JsonRecord,
  createdAt: string
) => {
  if (!isRecord(qualityGate)) {
    return;
  }

  const db = getDb();
  db.prepare(
    `
      INSERT INTO card_quality_gates (
        id,
        card_id,
        passed,
        quality_level,
        gate_name,
        gate_version,
        errors_json,
        warnings_json,
        raw_json,
        created_at
      )
      VALUES (
        @id,
        @card_id,
        @passed,
        @quality_level,
        @gate_name,
        @gate_version,
        @errors_json,
        @warnings_json,
        @raw_json,
        @created_at
      )
    `
  ).run({
    id: `gate_${randomUUID()}`,
    card_id: cardId,
    passed: qualityGate.passed === true ? 1 : 0,
    quality_level:
      asLooseString(qualityGate.quality_level) || asLooseString(card.quality_level),
    gate_name: asLooseString(qualityGate.gate_name),
    gate_version: asLooseString(qualityGate.gate_version),
    errors_json: jsonOrNull(qualityGate.errors),
    warnings_json: jsonOrNull(qualityGate.warnings),
    raw_json: JSON.stringify(qualityGate),
    created_at: createdAt
  });
};

const insertRenderedViews = (
  cardId: string,
  renderedViews: unknown,
  markdown: string,
  plainText: string,
  createdAt: string
) => {
  const db = getDb();
  const record = isRecord(renderedViews) ? renderedViews : {};
  const viewEntries = new Map<string, string>();
  viewEntries.set('markdown', asLooseString(record.markdown) || markdown);
  viewEntries.set('plain_text', asLooseString(record.plain_text) || plainText);

  Object.entries(record).forEach(([key, value]) => {
    if (key === 'markdown' || key === 'plain_text') {
      return;
    }
    const content = asLooseString(value);
    if (content) {
      viewEntries.set(key, content);
    }
  });

  viewEntries.forEach((content, viewType) => {
    db.prepare(
      `
        INSERT INTO card_rendered_views (
          id,
          card_id,
          view_type,
          content,
          renderer_name,
          renderer_version,
          created_at,
          updated_at
        )
        VALUES (
          @id,
          @card_id,
          @view_type,
          @content,
          @renderer_name,
          @renderer_version,
          @created_at,
          @updated_at
        )
      `
    ).run({
      id: `view_${randomUUID()}`,
      card_id: cardId,
      view_type: viewType,
      content,
      renderer_name: 'lucas-db-v0',
      renderer_version: '1',
      created_at: createdAt,
      updated_at: createdAt
    });
  });
};

const renderedViewEntries = (
  renderedViews: unknown,
  markdown: string,
  plainText: string
) => {
  const record = isRecord(renderedViews) ? renderedViews : {};
  const viewEntries = new Map<string, string>();
  viewEntries.set('markdown', asLooseString(record.markdown) || markdown);
  viewEntries.set('plain_text', asLooseString(record.plain_text) || plainText);

  Object.entries(record).forEach(([key, value]) => {
    if (key === 'markdown' || key === 'plain_text') {
      return;
    }
    const content = asLooseString(value);
    if (content) {
      viewEntries.set(key, content);
    }
  });

  return viewEntries;
};

const upsertRenderedViews = (
  cardId: string,
  renderedViews: unknown,
  markdown: string,
  plainText: string,
  updatedAt: string
) => {
  const db = getDb();
  let changed = false;
  const viewEntries = renderedViewEntries(renderedViews, markdown, plainText);

  viewEntries.forEach((content, viewType) => {
    const existing = db
      .prepare(
        `
          SELECT id, content
          FROM card_rendered_views
          WHERE card_id = ? AND view_type = ?
          ORDER BY updated_at DESC, created_at DESC
          LIMIT 1
        `
      )
      .get(cardId, viewType) as Pick<CardRenderedViewRow, 'id' | 'content'> | undefined;

    if (!existing) {
      db.prepare(
        `
          INSERT INTO card_rendered_views (
            id,
            card_id,
            view_type,
            content,
            renderer_name,
            renderer_version,
            created_at,
            updated_at
          )
          VALUES (
            @id,
            @card_id,
            @view_type,
            @content,
            @renderer_name,
            @renderer_version,
            @created_at,
            @updated_at
          )
        `
      ).run({
        id: `view_${randomUUID()}`,
        card_id: cardId,
        view_type: viewType,
        content,
        renderer_name: 'lucas-db-v0',
        renderer_version: '1',
        created_at: updatedAt,
        updated_at: updatedAt
      });
      changed = true;
      return;
    }

    if (existing.content !== content) {
      db.prepare(
        `
          UPDATE card_rendered_views
          SET content = @content,
              updated_at = @updated_at
          WHERE id = @id
        `
      ).run({
        id: existing.id,
        content,
        updated_at: updatedAt
      });
      changed = true;
    }
  });

  return changed;
};

const insertRelation = (relation: PreparedRelation) => {
  const db = getDb();
  const now = new Date().toISOString();
  db.prepare(
    `
      INSERT INTO node_relations (
        id,
        from_node_id,
        to_node_id,
        from_card_id,
        to_card_id,
        relation_type,
        label,
        source,
        confidence,
        metadata_json,
        created_at,
        updated_at
      )
      VALUES (
        @id,
        @from_node_id,
        @to_node_id,
        @from_card_id,
        @to_card_id,
        @relation_type,
        @label,
        @source,
        @confidence,
        @metadata_json,
        @created_at,
        @updated_at
      )
    `
  ).run({
    id: `rel_${randomUUID()}`,
    ...relation,
    created_at: now,
    updated_at: now
  });
};

const makeMetadata = (type: string, value: unknown, index: number) =>
  JSON.stringify({
    virtual_target: {
      type,
      label: valueToContent(value) || String(value),
      sort_order: index
    }
  });

const makeSystemRelations = (
  nodeId: string,
  cardId: string,
  card: JsonRecord,
  sourceMaterial: unknown
): PreparedRelation[] => {
  const relations: PreparedRelation[] = [];
  const addVirtual = (
    relationType: string,
    virtualType: string,
    label: string,
    value: unknown,
    index: number
  ) => {
    relations.push({
      from_node_id: nodeId,
      to_node_id: null,
      from_card_id: cardId,
      to_card_id: null,
      relation_type: relationType,
      label,
      source: 'card_mapping',
      confidence: 1,
      metadata_json: makeMetadata(virtualType, value, index)
    });
  };

  getArray(card, 'knowledge_blocks')
    .filter(isRecord)
    .forEach((block, index) => {
      const concept = asString(block.concept);
      if (concept) {
        addVirtual('HAS_CONCEPT', 'concept', concept, concept, index);
      }
    });

  getArray(card, 'tags').forEach((value, index) => {
    const content = valueToContent(value);
    if (content) {
      addVirtual('TAGGED_AS', 'tag', content, content, index);
    }
  });

  getArray(card, 'risks').forEach((value, index) => {
    const content = valueToContent(value);
    if (content) {
      addVirtual('HAS_RISK', 'risk', content, content, index);
    }
  });

  getArray(card, 'follow_up_actions').forEach((value, index) => {
    const content = valueToContent(value);
    if (content) {
      addVirtual('SUGGESTS_ACTION', 'action', content, content, index);
    }
  });

  getArray(card, 'methodology').forEach((value, index) => {
    const content = valueToContent(value);
    if (content) {
      addVirtual('HAS_METHOD', 'method', content, content, index);
    }
  });

  const modelUsed = asString(card.model_used);
  if (modelUsed) {
    addVirtual('GENERATED_BY_MODEL', 'model', modelUsed, modelUsed, 0);
  }

  const sourceTitle =
    asString(card.source_title) ||
    (isRecord(sourceMaterial)
      ? asString(sourceMaterial.source_title) || asString(sourceMaterial.raw_title)
      : null);
  if (sourceTitle) {
    addVirtual('DERIVED_FROM_SOURCE', 'source', sourceTitle, sourceTitle, 0);
  }

  return relations;
};

const normalizeManualRelations = (
  value: unknown,
  nodeId: string,
  cardId: string
): PreparedRelation[] => {
  if (!Array.isArray(value)) {
    return [];
  }

  const normalized: Array<PreparedRelation | null> = value
    .filter(isRecord)
    .map((relation): PreparedRelation | null => {
      const relationType = asString(relation.relation_type) || asString(relation.type);
      if (!relationType) {
        return null;
      }
      return {
        from_node_id: asString(relation.from_node_id) || nodeId,
        to_node_id: asString(relation.to_node_id),
        from_card_id: asString(relation.from_card_id) || cardId,
        to_card_id: asString(relation.to_card_id),
        relation_type: relationType,
        label: asLooseString(relation.label),
        source: asString(relation.source) || 'agent',
        confidence:
          typeof relation.confidence === 'number' && Number.isFinite(relation.confidence)
            ? relation.confidence
            : 1,
        metadata_json: jsonOrNull(relation.metadata)
      };
    });

  return normalized.filter((relation): relation is PreparedRelation => relation !== null);
};

const normalizeFramePathKey = (value: string) =>
  value.trim().replace(/^<|>$/g, '').replace(/\\/g, '/').toLowerCase();

const rememberFrameAssetUrl = (
  framePathToAssetUrl: Map<string, string>,
  framePath: string,
  assetUrl: string
) => {
  framePathToAssetUrl.set(framePath, assetUrl);
  framePathToAssetUrl.set(framePath.replace(/\\/g, '/'), assetUrl);
  framePathToAssetUrl.set(normalizeFramePathKey(framePath), assetUrl);
};

const rememberAssetUrlAlias = (
  framePathToAssetUrl: Map<string, string>,
  aliasUrl: unknown,
  assetUrl: string
) => {
  const alias = asString(aliasUrl);
  if (!alias || alias === assetUrl) {
    return;
  }
  framePathToAssetUrl.set(alias, assetUrl);
  framePathToAssetUrl.set(normalizeFramePathKey(alias), assetUrl);
};

const isLucasAssetUrl = (value: unknown) => {
  const url = asString(value);
  if (!url) {
    return false;
  }
  return /^\/api\/assets\/[^/?#]+/i.test(url) || /^https?:\/\/[^/]+\/api\/assets\/[^/?#]+/i.test(url);
};

interface ExistingOcrFrameAsset {
  assetUrl: string;
  attachmentId?: string;
}

const rememberExistingOcrFrameAsset = (
  frameAssets: Map<string, ExistingOcrFrameAsset>,
  framePath: unknown,
  asset: ExistingOcrFrameAsset
) => {
  const path = asString(framePath);
  if (!path) {
    return;
  }
  frameAssets.set(path, asset);
  frameAssets.set(path.replace(/\\/g, '/'), asset);
  frameAssets.set(normalizeFramePathKey(path), asset);
};

const existingOcrFrameAssetsForCard = (cardId: string) => {
  const db = getDb();
  const row = db
    .prepare(
      `
        SELECT metadata_json
        FROM card_source_materials
        WHERE card_id = ?
        ORDER BY created_at DESC
        LIMIT 1
      `
    )
    .get(cardId) as { metadata_json: string | null } | undefined;

  const frameAssets = new Map<string, ExistingOcrFrameAsset>();
  if (!row?.metadata_json) {
    return frameAssets;
  }

  try {
    const metadata = JSON.parse(row.metadata_json) as unknown;
    if (!isRecord(metadata) || !Array.isArray(metadata.ocr_evidence_items)) {
      return frameAssets;
    }
    metadata.ocr_evidence_items.filter(isRecord).forEach((item) => {
      const assetUrl = asString(item.asset_url);
      if (!assetUrl || !isLucasAssetUrl(assetUrl)) {
        return;
      }
      rememberExistingOcrFrameAsset(frameAssets, item.frame_path, {
        assetUrl,
        attachmentId: asString(item.attachment_id) || undefined
      });
    });
  } catch {
    return frameAssets;
  }

  return frameAssets;
};

const replaceFramePathText = (value: string, framePathToAssetUrl: Map<string, string>) => {
  let output = value;
  framePathToAssetUrl.forEach((assetUrl, framePath) => {
    output = output.split(framePath).join(assetUrl);
  });
  return output;
};

const rewriteMarkdownFrameLinks = (
  markdown: string,
  framePathToAssetUrl: Map<string, string>
) => {
  if (framePathToAssetUrl.size === 0) {
    return markdown;
  }

  let output = markdown.replace(
    /!\[([^\]]*)\]\((<)?([^>\)\n]+)(>)?\)/g,
    (match, altText: string, _open: string | undefined, url: string) => {
      const assetUrl = framePathToAssetUrl.get(normalizeFramePathKey(url));
      return assetUrl ? `![${altText}](${assetUrl})` : match;
    }
  );

  framePathToAssetUrl.forEach((assetUrl, framePath) => {
    output = output.split(framePath).join(assetUrl);
    output = output.split(`<${framePath}>`).join(assetUrl);
  });

  return output;
};

const applyFrameAssetUrlsToRenderedViews = (
  renderedViews: unknown,
  markdown: string,
  plainText: string,
  framePathToAssetUrl: Map<string, string>
): {
  rendered_views: JsonRecord;
  markdown: string;
  plain_text: string;
} => {
  const rewrittenMarkdown = rewriteMarkdownFrameLinks(markdown, framePathToAssetUrl);
  const rewrittenPlainText = replaceFramePathText(plainText, framePathToAssetUrl);

  if (!isRecord(renderedViews)) {
    return {
      rendered_views: {
        markdown: rewrittenMarkdown,
        plain_text: rewrittenPlainText
      },
      markdown: rewrittenMarkdown,
      plain_text: rewrittenPlainText
    };
  }

  const nextRenderedViews: JsonRecord = { ...renderedViews };
  Object.entries(nextRenderedViews).forEach(([key, value]) => {
    if (typeof value !== 'string') {
      return;
    }

    nextRenderedViews[key] =
      key === 'markdown'
        ? rewriteMarkdownFrameLinks(value, framePathToAssetUrl)
        : replaceFramePathText(value, framePathToAssetUrl);
  });
  nextRenderedViews.markdown = rewriteMarkdownFrameLinks(
    asLooseString(nextRenderedViews.markdown) || rewrittenMarkdown,
    framePathToAssetUrl
  );
  nextRenderedViews.plain_text = replaceFramePathText(
    asLooseString(nextRenderedViews.plain_text) || rewrittenPlainText,
    framePathToAssetUrl
  );

  return {
    rendered_views: nextRenderedViews,
    markdown: asLooseString(nextRenderedViews.markdown) || rewrittenMarkdown,
    plain_text: asLooseString(nextRenderedViews.plain_text) || rewrittenPlainText
  };
};

const buildSourceMaterialMetadata = (sourceMaterial: JsonRecord) => {
  const metadata = isRecord(sourceMaterial.metadata) ? { ...sourceMaterial.metadata } : {};
  if (Array.isArray(sourceMaterial.ocr_evidence_items)) {
    metadata.ocr_evidence_items = sourceMaterial.ocr_evidence_items;
  }
  return Object.keys(metadata).length > 0 ? metadata : undefined;
};

const getOcrEvidenceItems = (sourceMaterial: JsonRecord) => {
  if (Array.isArray(sourceMaterial.ocr_evidence_items)) {
    return sourceMaterial.ocr_evidence_items;
  }

  if (isRecord(sourceMaterial.metadata) && Array.isArray(sourceMaterial.metadata.ocr_evidence_items)) {
    return sourceMaterial.metadata.ocr_evidence_items;
  }

  return null;
};

const assertOcrFramePathsReadable = (sourceMaterial: unknown) => {
  if (!isRecord(sourceMaterial)) {
    return;
  }

  const evidenceItems = getOcrEvidenceItems(sourceMaterial);
  if (!evidenceItems) {
    return;
  }

  evidenceItems.filter(isRecord).forEach((item) => {
    if (item.image_worth_saving !== true || asString(item.asset_url)) {
      return;
    }

    const framePath = asString(item.frame_path);
    if (!framePath) {
      return;
    }

    if (!fs.existsSync(framePath)) {
      throw new ApiError('OCR_FRAME_NOT_FOUND', `OCR frame not found: ${framePath}`, 400);
    }
  });
};

const prepareOcrEvidenceAssets = (input: {
  sourceMaterial: unknown;
  nodeId: string;
  actor: string;
  copiedAttachments: NodeAttachment[];
  existingFrameAssets?: Map<string, ExistingOcrFrameAsset>;
}) => {
  if (!isRecord(input.sourceMaterial)) {
    return {
      source_material: input.sourceMaterial,
      framePathToAssetUrl: new Map<string, string>()
    };
  }

  const sourceMaterial = { ...input.sourceMaterial };
  const framePathToAssetUrl = new Map<string, string>();
  const evidenceItems = getOcrEvidenceItems(sourceMaterial);

  if (!evidenceItems) {
    return {
      source_material: sourceMaterial,
      framePathToAssetUrl
    };
  }

  const enhancedItems = evidenceItems.map((item) => {
    if (!isRecord(item)) {
      return item;
    }

    const framePath = asString(item.frame_path);
    if (!framePath) {
      return item;
    }

    if (item.image_worth_saving !== true) {
      return item;
    }

    const originalAssetUrl = asString(item.asset_url);
    const existingAsset = input.existingFrameAssets?.get(normalizeFramePathKey(framePath));
    if (existingAsset) {
      rememberFrameAssetUrl(framePathToAssetUrl, framePath, existingAsset.assetUrl);
      rememberAssetUrlAlias(framePathToAssetUrl, originalAssetUrl, existingAsset.assetUrl);
      return {
        ...item,
        frame_path: framePath,
        asset_url: existingAsset.assetUrl,
        attachment_id: existingAsset.attachmentId || asString(item.attachment_id) || undefined
      };
    }

    if (originalAssetUrl && isLucasAssetUrl(originalAssetUrl)) {
      rememberFrameAssetUrl(framePathToAssetUrl, framePath, originalAssetUrl);
      return item;
    }

    if (!fs.existsSync(framePath)) {
      if (originalAssetUrl) {
        rememberFrameAssetUrl(framePathToAssetUrl, framePath, originalAssetUrl);
        return item;
      }
      throw new ApiError('OCR_FRAME_NOT_FOUND', `OCR frame not found: ${framePath}`, 400);
    }

    const attachment = copyLocalFileToAttachment(
      {
        nodeId: input.nodeId,
        filePath: framePath,
        actor: input.actor
      },
      { transaction: false }
    );
    input.copiedAttachments.push(attachment);
    const assetUrl = getAssetUrl(attachment.id);
    rememberFrameAssetUrl(framePathToAssetUrl, framePath, assetUrl);
    rememberAssetUrlAlias(framePathToAssetUrl, originalAssetUrl, assetUrl);

    return {
      ...item,
      frame_path: framePath,
      asset_url: assetUrl,
      attachment_id: attachment.id
    };
  });
  sourceMaterial.ocr_evidence_items = enhancedItems;
  sourceMaterial.metadata = {
    ...(isRecord(sourceMaterial.metadata) ? sourceMaterial.metadata : {}),
    ocr_evidence_items: enhancedItems
  };

  return {
    source_material: sourceMaterial,
    framePathToAssetUrl
  };
};

const refreshLatestSourceMaterialOcrMetadata = (
  cardId: string,
  sourceMaterial: unknown
) => {
  if (!isRecord(sourceMaterial) || !isRecord(sourceMaterial.metadata)) {
    return false;
  }

  const db = getDb();
  const row = db
    .prepare(
      `
        SELECT id, metadata_json
        FROM card_source_materials
        WHERE card_id = ?
        ORDER BY created_at DESC
        LIMIT 1
      `
    )
    .get(cardId) as { id: string; metadata_json: string | null } | undefined;
  if (!row) {
    return false;
  }

  const nextMetadata = JSON.stringify(sourceMaterial.metadata);
  if (row.metadata_json === nextMetadata) {
    return false;
  }

  db.prepare(
    `
      UPDATE card_source_materials
      SET metadata_json = @metadata_json
      WHERE id = @id
    `
  ).run({
    id: row.id,
    metadata_json: nextMetadata
  });
  return true;
};

const updateNodeContentInActiveTransaction = (
  before: NodeRow,
  content: string,
  actor: string
) => {
  if (before.content === content) {
    return before;
  }

  const after: NodeRow = {
    ...before,
    content,
    updated_at: new Date().toISOString()
  };
  const db = getDb();
  db.prepare(
    `
      UPDATE nodes
      SET content = @content,
          updated_at = @updated_at
      WHERE id = @id AND deleted_at IS NULL
    `
  ).run({
    id: after.id,
    content: after.content,
    updated_at: after.updated_at
  });

  recordNodeEvent({
    nodeId: before.id,
    actor,
    action: 'update',
    beforeSnapshot: before,
    afterSnapshot: after
  });

  return after;
};

export const ingestCard = (input: IngestInput): IngestReceipt => {
  const card = requireCard(input.card);
  const qualityGate = requireQualityGatePassed(input.quality_gate);
  const actor = asString(input.actor) || 'agent';
  const { segments, path } = normalizePath(input.target_path);
  const idempotencyKey = resolveIdempotencyKey(input);
  const existingCard = idempotencyKey ? findCardByIdempotencyKey(idempotencyKey) : undefined;
  assertOcrFramePathsReadable(input.source_material);

  const rendered = isRecord(input.rendered_views) ? input.rendered_views : {};
  const markdown = asLooseString(rendered.markdown) || makeDefaultMarkdown(card);
  const plainText = asLooseString(rendered.plain_text) || makePlainText(card, markdown);
  const nodeContent = plainText || markdown;
  const now = new Date().toISOString();
  const db = getDb();
  const copiedAttachments: NodeAttachment[] = [];
  const cleanupCopiedAttachments = () => {
    copiedAttachments.forEach((attachment) => {
      fs.rmSync(getAttachmentFilePath(attachment), { force: true });
    });
  };

  if (existingCard) {
    try {
      const updated = withTransaction(() => {
        const ocrAssets = prepareOcrEvidenceAssets({
          sourceMaterial: input.source_material,
          nodeId: existingCard.node_id,
          actor,
          copiedAttachments,
          existingFrameAssets: existingOcrFrameAssetsForCard(existingCard.id)
        });
        const renderedWithAssetUrls = applyFrameAssetUrlsToRenderedViews(
          input.rendered_views,
          markdown,
          plainText,
          ocrAssets.framePathToAssetUrl
        );
        const viewsChanged = upsertRenderedViews(
          existingCard.id,
          renderedWithAssetUrls.rendered_views,
          renderedWithAssetUrls.markdown,
          renderedWithAssetUrls.plain_text,
          now
        );
        const sourceMaterialChanged = refreshLatestSourceMaterialOcrMetadata(
          existingCard.id,
          ocrAssets.source_material
        );
        const changed = viewsChanged || sourceMaterialChanged || copiedAttachments.length > 0;

        if (changed) {
          db.prepare(
            `
              UPDATE cards
              SET updated_at = @updated_at
              WHERE id = @id AND deleted_at IS NULL
            `
          ).run({
            id: existingCard.id,
            updated_at: now
          });
          insertCardEvent({
            cardId: existingCard.id,
            nodeId: existingCard.node_id,
            actor,
            action: 'refresh_rendered_views',
            afterSnapshot: {
              card_id: existingCard.id,
              node_id: existingCard.node_id,
              target_path: path,
              idempotency_key: idempotencyKey,
              refreshed_views: viewsChanged,
              refreshed_source_material_ocr_assets: sourceMaterialChanged,
              ocr_assets_created: copiedAttachments.length
            }
          });
        }

        return changed;
      });

      return makeIngestReceipt({
        cardId: existingCard.id,
        nodeId: existingCard.node_id,
        path: buildNodePath(existingCard.node_id) || path,
        created: false,
        updated
      });
    } catch (error) {
      cleanupCopiedAttachments();
      throw error;
    }
  }
  const node = ensurePathNode(segments, nodeContent, actor);

  try {
    return withTransaction(() => {
      const cardId = `card_${randomUUID()}`;
      const ocrAssets = prepareOcrEvidenceAssets({
        sourceMaterial: input.source_material,
        nodeId: node.id,
        actor,
        copiedAttachments
      });
      const renderedWithAssetUrls = applyFrameAssetUrlsToRenderedViews(
        input.rendered_views,
        markdown,
        plainText,
        ocrAssets.framePathToAssetUrl
      );
      const finalMarkdown = renderedWithAssetUrls.markdown;
      const finalPlainText = renderedWithAssetUrls.plain_text;
      const finalNodeContent = finalPlainText || finalMarkdown;
      const finalNode = updateNodeContentInActiveTransaction(node, finalNodeContent, actor);
      const displayTitle =
        asString(card.display_title) ||
        asString(card.source_title) ||
        segments[segments.length - 1] ||
        '未命名卡片';
      const qualityLevel =
        asLooseString(card.quality_level) ||
        (isRecord(input.quality_gate) ? asLooseString(input.quality_gate.quality_level) : null);

      db.prepare(
        `
        INSERT INTO cards (
          id,
          node_id,
          schema_name,
          schema_version,
          card_type,
          content_level,
          quality_level,
          source_title,
          display_title,
          safe_filename_title,
          one_sentence_summary,
          original_summary,
          idempotency_key,
          raw_json,
          composer_status,
          composer_error,
          model_used,
          model_provider,
          created_at,
          updated_at,
          deleted_at
        )
        VALUES (
          @id,
          @node_id,
          @schema_name,
          @schema_version,
          @card_type,
          @content_level,
          @quality_level,
          @source_title,
          @display_title,
          @safe_filename_title,
          @one_sentence_summary,
          @original_summary,
          @idempotency_key,
          @raw_json,
          @composer_status,
          @composer_error,
          @model_used,
          @model_provider,
          @created_at,
          @updated_at,
          @deleted_at
        )
      `
      ).run({
        id: cardId,
        node_id: finalNode.id,
        schema_name: asString(card.schema_name),
        schema_version: asString(card.schema_version),
        card_type: asLooseString(card.card_type),
        content_level: asLooseString(card.content_level),
        quality_level: qualityLevel,
        source_title: asLooseString(card.source_title),
        display_title: displayTitle,
        safe_filename_title: asLooseString(card.safe_filename_title),
        one_sentence_summary: asLooseString(card.one_sentence_summary),
        original_summary: asLooseString(card.original_summary),
        idempotency_key: idempotencyKey,
        raw_json: JSON.stringify(card),
        composer_status: asLooseString(card.composer_status),
        composer_error: asLooseString(card.composer_error),
        model_used: asLooseString(card.model_used),
        model_provider: asLooseString(card.model_provider),
        created_at: now,
        updated_at: now,
        deleted_at: null
      });

      insertKnowledgeBlocks(cardId, card, now);
      insertItems(cardId, card, now);
      insertCommentSignals(cardId, card, now);
      insertSourceMaterial(cardId, ocrAssets.source_material, card, now);
      insertQualityGate(cardId, qualityGate, card, now);
      insertRenderedViews(
        cardId,
        renderedWithAssetUrls.rendered_views,
        finalMarkdown,
        finalPlainText,
        now
      );

      const relations = [
        ...makeSystemRelations(finalNode.id, cardId, card, ocrAssets.source_material),
        ...normalizeManualRelations(input.relations, finalNode.id, cardId)
      ];
      relations.forEach(insertRelation);

      insertCardEvent({
        cardId,
        nodeId: finalNode.id,
        actor,
        action: 'ingest',
        afterSnapshot: {
          card_id: cardId,
          node_id: finalNode.id,
          target_path: path,
          idempotency_key: idempotencyKey,
          graph_edges_created: relations.length,
          ocr_assets_created: copiedAttachments.length
        }
      });

      return makeIngestReceipt({
        cardId,
        nodeId: finalNode.id,
        path,
        created: true
      });
    });
  } catch (error) {
    cleanupCopiedAttachments();
    throw error;
  }
};

export const getCardOrThrow = (id: string): CardRow => {
  const db = getDb();
  const row = db
    .prepare('SELECT * FROM cards WHERE id = ? AND deleted_at IS NULL')
    .get(id) as CardRow | undefined;
  if (!row) {
    throw new ApiError('CARD_NOT_FOUND', 'Card not found', 404);
  }
  return row;
};

export const getCardsByNode = (nodeId: string) => {
  getNodeOrThrow(nodeId);
  const db = getDb();
  const cards = db
    .prepare(
      `
        SELECT *
        FROM cards
        WHERE node_id = ? AND deleted_at IS NULL
        ORDER BY updated_at DESC, created_at DESC
      `
    )
    .all(nodeId) as unknown as CardRow[];

  return {
    ok: true,
    node_id: nodeId,
    cards: cards.map((card) => ({
      ...card,
      markdown_view:
        (
          db
            .prepare(
              `
                SELECT content
                FROM card_rendered_views
                WHERE card_id = ? AND view_type = 'markdown'
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
              `
            )
            .get(card.id) as { content: string } | undefined
        )?.content || null,
      plain_text_view:
        (
          db
            .prepare(
              `
                SELECT content
                FROM card_rendered_views
                WHERE card_id = ? AND view_type = 'plain_text'
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
              `
            )
            .get(card.id) as { content: string } | undefined
        )?.content || null,
      tags: (
        db
          .prepare(
            `
              SELECT content
              FROM card_items
              WHERE card_id = ? AND item_type = 'tag'
              ORDER BY sort_order
            `
          )
          .all(card.id) as Array<{ content: string }>
      ).map((item) => item.content)
    }))
  };
};

export const getCardDetail = (id: string): CardDetail => {
  const card = getCardOrThrow(id);
  const db = getDb();
  return {
    ok: true,
    card: {
      ...card,
      raw_json_parsed: JSON.parse(card.raw_json)
    },
    knowledge_blocks: db
      .prepare('SELECT * FROM card_knowledge_blocks WHERE card_id = ? ORDER BY sort_order')
      .all(id) as unknown as CardKnowledgeBlockRow[],
    items: db
      .prepare('SELECT * FROM card_items WHERE card_id = ? ORDER BY item_type, sort_order')
      .all(id) as unknown as CardItemRow[],
    comment_signals: db
      .prepare('SELECT * FROM card_comment_signals WHERE card_id = ? ORDER BY created_at')
      .all(id) as unknown as CardCommentSignalRow[],
    source_materials: db
      .prepare('SELECT * FROM card_source_materials WHERE card_id = ? ORDER BY created_at')
      .all(id) as unknown as CardSourceMaterialRow[],
    quality_gates: db
      .prepare('SELECT * FROM card_quality_gates WHERE card_id = ? ORDER BY created_at')
      .all(id) as unknown as CardQualityGateRow[],
    rendered_views: db
      .prepare('SELECT * FROM card_rendered_views WHERE card_id = ? ORDER BY view_type')
      .all(id) as unknown as CardRenderedViewRow[],
    relations: db
      .prepare(
        `
          SELECT *
          FROM node_relations
          WHERE deleted_at IS NULL
            AND (from_card_id = ? OR to_card_id = ?)
          ORDER BY created_at
        `
      )
      .all(id, id) as unknown as NodeRelationRow[]
  };
};

export const createManualRelation = (input: unknown) => {
  if (!isRecord(input)) {
    throw new ApiError('RELATION_REQUIRED', 'relation input is required', 400);
  }

  const relationType = asString(input.relation_type);
  if (!relationType) {
    throw new ApiError('RELATION_TYPE_REQUIRED', 'relation_type is required', 400);
  }

  const fromNodeId = asString(input.from_node_id);
  const toNodeId = asString(input.to_node_id);
  const fromCardId = asString(input.from_card_id);
  const toCardId = asString(input.to_card_id);

  if (!fromNodeId && !fromCardId) {
    throw new ApiError('RELATION_FROM_REQUIRED', 'from_node_id or from_card_id is required', 400);
  }
  if (!toNodeId && !toCardId && !input.metadata) {
    throw new ApiError(
      'RELATION_TO_REQUIRED',
      'to_node_id, to_card_id, or metadata is required',
      400
    );
  }

  const now = new Date().toISOString();
  const id = `rel_${randomUUID()}`;
  const db = getDb();
  db.prepare(
    `
      INSERT INTO node_relations (
        id,
        from_node_id,
        to_node_id,
        from_card_id,
        to_card_id,
        relation_type,
        label,
        source,
        confidence,
        metadata_json,
        created_at,
        updated_at
      )
      VALUES (
        @id,
        @from_node_id,
        @to_node_id,
        @from_card_id,
        @to_card_id,
        @relation_type,
        @label,
        @source,
        @confidence,
        @metadata_json,
        @created_at,
        @updated_at
      )
    `
  ).run({
    id,
    from_node_id: fromNodeId,
    to_node_id: toNodeId,
    from_card_id: fromCardId,
    to_card_id: toCardId,
    relation_type: relationType,
    label: asLooseString(input.label),
    source: asString(input.source) || 'manual',
    confidence:
      typeof input.confidence === 'number' && Number.isFinite(input.confidence)
        ? input.confidence
        : 1,
    metadata_json: jsonOrNull(input.metadata),
    created_at: now,
    updated_at: now
  });

  return {
    ok: true,
    id,
    created_at: now
  };
};
