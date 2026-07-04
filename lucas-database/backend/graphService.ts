import { createHash, randomUUID } from 'node:crypto';
import { getDb, withTransaction } from './db';
import { ApiError } from './errors';
import type {
  CardCommentSignalRow,
  CardItemRow,
  CardKnowledgeBlockRow,
  CardRow,
  CardSourceMaterialRow,
  GraphData,
  GraphEdge,
  GraphNode,
  NodeRelationRow
} from './cardTypes';
import type { NodeRow } from './types';

const GENERATED_SOURCES = ['card_mapping', 'system_rule'];

const relationLabels: Record<string, string> = {
  MOUNTED_ON: '挂载到',
  HAS_CONCEPT: '包含概念',
  TAGGED_AS: '标记为',
  HAS_RISK: '存在风险',
  SUGGESTS_ACTION: '建议动作',
  HAS_METHOD: '包含方法',
  DERIVED_FROM_SOURCE: '来源于',
  GENERATED_BY_MODEL: '由模型生成'
};

interface GraphQueryInput {
  nodeId?: unknown;
  depth?: unknown;
  scope?: unknown;
}

interface CreateRelationInput {
  from_node_id?: unknown;
  to_node_id?: unknown;
  from_card_id?: unknown;
  to_card_id?: unknown;
  relation_type?: unknown;
  label?: unknown;
  source?: unknown;
  confidence?: unknown;
  metadata?: unknown;
  metadata_json?: unknown;
}

interface GeneratedRelationInput {
  from_node_id?: string | null;
  to_node_id?: string | null;
  from_card_id?: string | null;
  to_card_id?: string | null;
  relation_type: string;
  label: string;
  source: string;
  confidence: number;
  metadata: Record<string, unknown> | null;
}

const nodeGraphId = (id: string) => `node:${id}`;
const cardGraphId = (id: string) => `card:${id}`;
const entityGraphId = (entityType: string, entityId: string) => `${entityType}:${entityId}`;

const hashText = (value: string) => createHash('sha1').update(value).digest('hex').slice(0, 16);

const makeEntityId = (entityType: string, label: string) =>
  `${entityType}_${hashText(`${entityType}:${label.trim().toLowerCase()}`)}`;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const asString = (value: unknown): string | null => {
  if (typeof value !== 'string') {
    return null;
  }

  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
};

const normalizeId = (value: unknown) => asString(value);

const normalizeDepth = (value: unknown) => {
  const numeric = typeof value === 'string' ? Number(value) : value;
  if (typeof numeric !== 'number' || !Number.isFinite(numeric)) {
    return 1;
  }
  return Math.max(0, Math.min(3, Math.floor(numeric)));
};

const normalizeScope = (value: unknown) => (value === 'global' ? 'global' : 'local');

const parseJsonObject = (value: string | null): Record<string, unknown> => {
  if (!value) {
    return {};
  }

  try {
    const parsed = JSON.parse(value);
    return isRecord(parsed) ? parsed : {};
  } catch {
    return {};
  }
};

const pickString = (record: Record<string, unknown>, keys: string[]) => {
  for (const key of keys) {
    const value = asString(record[key]);
    if (value) {
      return value;
    }
  }
  return null;
};

const valueToText = (value: unknown, keys: string[]): string | null => {
  const direct = asString(value);
  if (direct) {
    return direct;
  }

  if (isRecord(value)) {
    return pickString(value, keys);
  }

  return null;
};

const textArrayFromField = (
  raw: Record<string, unknown>,
  field: string,
  keys: string[]
): string[] => {
  const value = raw[field];
  if (Array.isArray(value)) {
    return value.map((item) => valueToText(item, keys)).filter((item): item is string => !!item);
  }

  const single = valueToText(value, keys);
  return single ? [single] : [];
};

const uniqueTexts = (values: Array<string | null | undefined>) => {
  const seen = new Set<string>();
  const result: string[] = [];

  values.forEach((value) => {
    const text = asString(value);
    if (!text) {
      return;
    }

    const key = text.toLowerCase();
    if (seen.has(key)) {
      return;
    }

    seen.add(key);
    result.push(text);
  });

  return result;
};

const makeInClause = (values: string[]) => values.map(() => '?').join(', ');

const getActiveNodeById = (id: string): NodeRow | undefined => {
  const db = getDb();
  return db
    .prepare('SELECT * FROM nodes WHERE id = ? AND deleted_at IS NULL')
    .get(id) as NodeRow | undefined;
};

const getActiveCardById = (id: string): CardRow | undefined => {
  const db = getDb();
  return db
    .prepare('SELECT * FROM cards WHERE id = ? AND deleted_at IS NULL')
    .get(id) as CardRow | undefined;
};

const getNodeOrThrow = (id: string): NodeRow => {
  const node = getActiveNodeById(id);
  if (!node) {
    throw new ApiError('NODE_NOT_FOUND', 'Node not found', 404);
  }
  return node;
};

const getCardOrThrow = (id: string): CardRow => {
  const card = getActiveCardById(id);
  if (!card) {
    throw new ApiError('CARD_NOT_FOUND', 'Card not found', 404);
  }
  return card;
};

const loadLatestNodes = (): NodeRow[] => {
  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM nodes
        WHERE deleted_at IS NULL
        ORDER BY updated_at DESC
        LIMIT 30
      `
    )
    .all() as unknown as NodeRow[];
};

const loadLatestCards = (): CardRow[] => {
  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM cards
        WHERE deleted_at IS NULL
        ORDER BY updated_at DESC
        LIMIT 30
      `
    )
    .all() as unknown as CardRow[];
};

const loadNodesByIds = (ids: Set<string>): NodeRow[] => {
  const values = [...ids];
  if (values.length === 0) {
    return [];
  }

  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM nodes
        WHERE deleted_at IS NULL
          AND id IN (${makeInClause(values)})
      `
    )
    .all(...values) as unknown as NodeRow[];
};

const loadCardsByIds = (ids: Set<string>): CardRow[] => {
  const values = [...ids];
  if (values.length === 0) {
    return [];
  }

  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM cards
        WHERE deleted_at IS NULL
          AND id IN (${makeInClause(values)})
      `
    )
    .all(...values) as unknown as CardRow[];
};

const expandNodeNeighborhood = (nodeIds: Set<string>, depth: number) => {
  if (nodeIds.size === 0 || depth <= 0) {
    return;
  }

  const db = getDb();
  let frontier = new Set(nodeIds);
  for (let step = 0; step < depth; step += 1) {
    const values = [...frontier];
    if (values.length === 0) {
      break;
    }

    const rows = db
      .prepare(
        `
          SELECT id, parent_id
          FROM nodes
          WHERE deleted_at IS NULL
            AND (
              id IN (${makeInClause(values)})
              OR parent_id IN (${makeInClause(values)})
            )
        `
      )
      .all(...values, ...values) as unknown as Pick<NodeRow, 'id' | 'parent_id'>[];
    const next = new Set<string>();

    rows.forEach((row) => {
      if (!nodeIds.has(row.id)) {
        nodeIds.add(row.id);
        next.add(row.id);
      }
      if (row.parent_id && !nodeIds.has(row.parent_id)) {
        nodeIds.add(row.parent_id);
        next.add(row.parent_id);
      }
    });

    frontier = next;
  }
};

const loadCardsByNodeIds = (ids: Set<string>): CardRow[] => {
  const values = [...ids];
  if (values.length === 0) {
    return [];
  }

  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM cards
        WHERE deleted_at IS NULL
          AND node_id IN (${makeInClause(values)})
        ORDER BY updated_at DESC
      `
    )
    .all(...values) as unknown as CardRow[];
};

const loadRelationsForKnownIds = (
  nodeIds: Set<string>,
  cardIds: Set<string>
): NodeRelationRow[] => {
  const clauses: string[] = [];
  const args: string[] = [];
  const nodes = [...nodeIds];
  const cards = [...cardIds];

  if (nodes.length > 0) {
    const nodePlaceholders = makeInClause(nodes);
    clauses.push(`from_node_id IN (${nodePlaceholders})`);
    args.push(...nodes);
    clauses.push(`to_node_id IN (${nodePlaceholders})`);
    args.push(...nodes);
  }

  if (cards.length > 0) {
    const cardPlaceholders = makeInClause(cards);
    clauses.push(`from_card_id IN (${cardPlaceholders})`);
    args.push(...cards);
    clauses.push(`to_card_id IN (${cardPlaceholders})`);
    args.push(...cards);
  }

  if (clauses.length === 0) {
    return [];
  }

  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM node_relations
        WHERE deleted_at IS NULL
          AND (${clauses.join(' OR ')})
        ORDER BY updated_at DESC
        LIMIT 300
      `
    )
    .all(...args) as unknown as NodeRelationRow[];
};

const relationMetadata = (relation: NodeRelationRow) => parseJsonObject(relation.metadata_json);

const virtualTarget = (metadata: Record<string, unknown>) => {
  const entityType = asString(metadata.target_entity_type);
  const entityId = asString(metadata.target_entity_id);
  const label = asString(metadata.target_label);
  if (entityType && entityId && label) {
    return {
      id: entityGraphId(entityType, entityId),
      entityType,
      sourceId: entityId,
      label
    };
  }

  const legacyTarget = metadata.virtual_target;
  if (!isRecord(legacyTarget)) {
    return null;
  }

  const legacyEntityType = asString(legacyTarget.type);
  const legacyLabel = asString(legacyTarget.label);
  if (!legacyEntityType || !legacyLabel) {
    return null;
  }

  const legacyEntityId = asString(legacyTarget.id) || makeEntityId(legacyEntityType, legacyLabel);
  return {
    id: entityGraphId(legacyEntityType, legacyEntityId),
    entityType: legacyEntityType,
    sourceId: legacyEntityId,
    label: legacyLabel
  };
};

const addGraphNode = (nodes: Map<string, GraphNode>, node: GraphNode) => {
  if (!nodes.has(node.id)) {
    nodes.set(node.id, node);
  }
};

export const getGraphData = (input: GraphQueryInput): GraphData => {
  const requestedNodeId = normalizeId(input.nodeId);
  const depth = normalizeDepth(input.depth);
  const scope = normalizeScope(input.scope);
  const knownNodeIds = new Set<string>();
  const knownCardIds = new Set<string>();
  const relationMap = new Map<string, NodeRelationRow>();

  if (requestedNodeId && scope === 'local') {
    knownNodeIds.add(getNodeOrThrow(requestedNodeId).id);
  } else {
    loadLatestNodes().forEach((node) => knownNodeIds.add(node.id));
    loadLatestCards().forEach((card) => {
      knownCardIds.add(card.id);
      knownNodeIds.add(card.node_id);
    });
  }

  expandNodeNeighborhood(knownNodeIds, Math.max(1, depth));

  const iterations = Math.max(1, depth);
  for (let step = 0; step < iterations; step += 1) {
    expandNodeNeighborhood(knownNodeIds, 1);
    loadCardsByNodeIds(knownNodeIds).forEach((card) => knownCardIds.add(card.id));

    const relationRows = loadRelationsForKnownIds(knownNodeIds, knownCardIds);
    const beforeNodeCount = knownNodeIds.size;
    const beforeCardCount = knownCardIds.size;

    relationRows.forEach((relation) => {
      relationMap.set(relation.id, relation);
      if (relation.from_node_id) {
        knownNodeIds.add(relation.from_node_id);
      }
      if (relation.to_node_id) {
        knownNodeIds.add(relation.to_node_id);
      }
      if (relation.from_card_id) {
        knownCardIds.add(relation.from_card_id);
      }
      if (relation.to_card_id) {
        knownCardIds.add(relation.to_card_id);
      }
    });

    if (knownNodeIds.size === beforeNodeCount && knownCardIds.size === beforeCardCount) {
      break;
    }
  }

  loadCardsByNodeIds(knownNodeIds).forEach((card) => knownCardIds.add(card.id));

  const graphNodes = new Map<string, GraphNode>();
  const activeNodes = loadNodesByIds(knownNodeIds);
  const activeCards = loadCardsByIds(knownCardIds);
  const activeNodeIds = new Set(activeNodes.map((node) => node.id));
  const activeCardIds = new Set(activeCards.map((card) => card.id));

  activeNodes.forEach((node) => {
    addGraphNode(graphNodes, {
      id: nodeGraphId(node.id),
      entity_type: 'node',
      label: node.title,
      source_id: node.id,
      metadata: {
        node_id: node.id,
        parent_id: node.parent_id,
        type: node.type
      }
    });
  });

  activeCards.forEach((card) => {
    addGraphNode(graphNodes, {
      id: cardGraphId(card.id),
      entity_type: 'card',
      label: card.display_title,
      source_id: card.id,
      metadata: {
        card_id: card.id,
        node_id: card.node_id,
        schema_name: card.schema_name,
        schema_version: card.schema_version,
        card_type: card.card_type
      }
    });
  });

  const edges: GraphEdge[] = [];
  activeNodes.forEach((node) => {
    if (!node.parent_id || !activeNodeIds.has(node.parent_id)) {
      return;
    }

    edges.push({
      id: `tree:${node.parent_id}:${node.id}`,
      from: nodeGraphId(node.parent_id),
      to: nodeGraphId(node.id),
      relation_type: 'CONTAINS',
      label: '包含',
      source: 'system_rule',
      confidence: 1,
      metadata: {
        source_field: 'nodes.parent_id',
        structural: true
      }
    });
  });

  [...relationMap.values()].forEach((relation) => {
    const metadata = relationMetadata(relation);
    const from = relation.from_card_id
      ? cardGraphId(relation.from_card_id)
      : relation.from_node_id
        ? nodeGraphId(relation.from_node_id)
        : null;
    const to = relation.to_card_id
      ? cardGraphId(relation.to_card_id)
      : relation.to_node_id
        ? nodeGraphId(relation.to_node_id)
        : virtualTarget(metadata)?.id || null;

    const target = virtualTarget(metadata);
    if (target) {
      addGraphNode(graphNodes, {
        id: target.id,
        entity_type: target.entityType,
        label: target.label,
        source_id: target.sourceId,
        metadata
      });
    }

    if (!from || !to || !graphNodes.has(from) || !graphNodes.has(to)) {
      return;
    }
    if (relation.from_node_id && !activeNodeIds.has(relation.from_node_id)) {
      return;
    }
    if (relation.to_node_id && !activeNodeIds.has(relation.to_node_id)) {
      return;
    }
    if (relation.from_card_id && !activeCardIds.has(relation.from_card_id)) {
      return;
    }
    if (relation.to_card_id && !activeCardIds.has(relation.to_card_id)) {
      return;
    }

    edges.push({
      id: relation.id,
      from,
      to,
      relation_type: relation.relation_type,
      label: relation.label,
      source: relation.source,
      confidence: relation.confidence,
      metadata
    });
  });

  return {
    ok: true,
    nodes: [...graphNodes.values()],
    edges
  };
};

const metadataToJson = (metadata: unknown, metadataJson: unknown) => {
  if (metadata !== undefined) {
    if (!isRecord(metadata)) {
      throw new ApiError('INVALID_METADATA', 'metadata must be an object', 400);
    }
    return JSON.stringify(metadata);
  }

  const raw = asString(metadataJson);
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw);
    if (!isRecord(parsed)) {
      throw new Error('metadata_json must be an object');
    }
    return JSON.stringify(parsed);
  } catch {
    throw new ApiError('INVALID_METADATA', 'metadata_json must be a JSON object', 400);
  }
};

export const createRelation = (input: CreateRelationInput): NodeRelationRow => {
  const fromNodeId = normalizeId(input.from_node_id);
  const toNodeId = normalizeId(input.to_node_id);
  const fromCardId = normalizeId(input.from_card_id);
  const toCardId = normalizeId(input.to_card_id);
  const relationType = asString(input.relation_type)?.toUpperCase();

  if (!relationType) {
    throw new ApiError('RELATION_TYPE_REQUIRED', 'relation_type is required', 400);
  }
  if (!fromNodeId && !fromCardId) {
    throw new ApiError('RELATION_ENDPOINT_REQUIRED', 'from_node_id or from_card_id is required', 400);
  }
  if (!toNodeId && !toCardId) {
    throw new ApiError('RELATION_ENDPOINT_REQUIRED', 'to_node_id or to_card_id is required', 400);
  }

  if (fromNodeId) {
    getNodeOrThrow(fromNodeId);
  }
  if (toNodeId) {
    getNodeOrThrow(toNodeId);
  }
  if (fromCardId) {
    getCardOrThrow(fromCardId);
  }
  if (toCardId) {
    getCardOrThrow(toCardId);
  }

  const confidence =
    typeof input.confidence === 'number' && Number.isFinite(input.confidence)
      ? Math.max(0, Math.min(1, input.confidence))
      : 1;
  const now = new Date().toISOString();
  const relation: NodeRelationRow = {
    id: `rel_${randomUUID()}`,
    from_node_id: fromNodeId,
    to_node_id: toNodeId,
    from_card_id: fromCardId,
    to_card_id: toCardId,
    relation_type: relationType,
    label: asString(input.label) || relationLabels[relationType] || relationType,
    source: asString(input.source) || 'manual',
    confidence,
    metadata_json: metadataToJson(input.metadata, input.metadata_json),
    created_at: now,
    updated_at: now,
    deleted_at: null
  };

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
        updated_at,
        deleted_at
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
        @updated_at,
        @deleted_at
      )
    `
  ).run(relation as unknown as Record<string, string | number | null>);

  return relation;
};

export const deleteRelation = (id: string) => {
  const db = getDb();
  const relation = db
    .prepare('SELECT * FROM node_relations WHERE id = ? AND deleted_at IS NULL')
    .get(id) as NodeRelationRow | undefined;

  if (!relation) {
    throw new ApiError('RELATION_NOT_FOUND', 'Relation not found', 404);
  }

  const deletedAt = new Date().toISOString();
  db.prepare(
    `
      UPDATE node_relations
      SET deleted_at = @deleted_at,
          updated_at = @deleted_at
      WHERE id = @id AND deleted_at IS NULL
    `
  ).run({ id, deleted_at: deletedAt });

  return {
    ok: true,
    id,
    deleted_at: deletedAt
  };
};

const loadKnowledgeBlocks = (cardId: string): CardKnowledgeBlockRow[] => {
  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM card_knowledge_blocks
        WHERE card_id = ?
        ORDER BY sort_order, created_at
      `
    )
    .all(cardId) as unknown as CardKnowledgeBlockRow[];
};

const loadCardItems = (cardId: string): CardItemRow[] => {
  const db = getDb();
  return db
    .prepare(
      `
        SELECT *
        FROM card_items
        WHERE card_id = ?
        ORDER BY item_type, sort_order, created_at
      `
    )
    .all(cardId) as unknown as CardItemRow[];
};

const loadCommentSignals = (cardId: string): CardCommentSignalRow[] => {
  const db = getDb();
  return db
    .prepare('SELECT * FROM card_comment_signals WHERE card_id = ? ORDER BY created_at')
    .all(cardId) as unknown as CardCommentSignalRow[];
};

const loadSourceMaterials = (cardId: string): CardSourceMaterialRow[] => {
  const db = getDb();
  return db
    .prepare('SELECT * FROM card_source_materials WHERE card_id = ? ORDER BY created_at')
    .all(cardId) as unknown as CardSourceMaterialRow[];
};

const itemTexts = (items: CardItemRow[], types: string[]) =>
  items
    .filter((item) => types.includes(item.item_type))
    .map((item) => item.content);

const addVirtualGeneratedRelation = (
  relations: GeneratedRelationInput[],
  seen: Set<string>,
  cardId: string,
  relationType: string,
  entityType: string,
  label: string,
  field: string,
  extraMetadata: Record<string, unknown> = {}
) => {
  const cleanLabel = asString(label);
  if (!cleanLabel) {
    return;
  }

  const entityId = makeEntityId(entityType, cleanLabel);
  const key = `${relationType}:${entityType}:${entityId}`;
  if (seen.has(key)) {
    return;
  }

  seen.add(key);
  relations.push({
    from_card_id: cardId,
    to_node_id: null,
    to_card_id: null,
    relation_type: relationType,
    label: relationLabels[relationType] || relationType,
    source: 'card_mapping',
    confidence: 1,
    metadata: {
      target_entity_type: entityType,
      target_entity_id: entityId,
      target_label: cleanLabel,
      source_field: field,
      ...extraMetadata
    }
  });
};

const sourceCandidates = (
  card: CardRow,
  raw: Record<string, unknown>,
  materials: CardSourceMaterialRow[],
  signals: CardCommentSignalRow[]
) => {
  const candidates: Array<{ label: string; metadata: Record<string, unknown> }> = [];
  const rawSourceTitle = asString(raw.source_title);
  const rawSourceUrl = asString(raw.source_url);
  const title = card.source_title || rawSourceTitle;

  if (title || rawSourceUrl) {
    candidates.push({
      label: title || rawSourceUrl || '',
      metadata: {
        source_title: title,
        source_url: rawSourceUrl
      }
    });
  }

  materials.forEach((material) => {
    const label = material.source_title || material.source_url || material.source_type;
    if (!label) {
      return;
    }
    candidates.push({
      label,
      metadata: {
        source_material_id: material.id,
        source_title: material.source_title,
        source_url: material.source_url,
        source_type: material.source_type
      }
    });
  });

  signals.forEach((signal) => {
    if (signal.comments_video_id) {
      candidates.push({
        label: signal.comments_video_id,
        metadata: {
          source_kind: 'comments_video_id',
          comments_video_id: signal.comments_video_id
        }
      });
    }
    if (signal.comments_job_id) {
      candidates.push({
        label: signal.comments_job_id,
        metadata: {
          source_kind: 'comments_job_id',
          comments_job_id: signal.comments_job_id
        }
      });
    }
  });

  return candidates;
};

const modelCandidate = (card: CardRow, raw: Record<string, unknown>) => {
  const provider = card.model_provider || asString(raw.model_provider);
  const model = card.model_used || asString(raw.model_used);

  if (!provider && !model) {
    return null;
  }

  return {
    label: provider && model ? `${provider}/${model}` : provider || model || '',
    metadata: {
      model_provider: provider,
      model_used: model
    }
  };
};

const insertGeneratedRelations = (
  card: CardRow,
  relations: GeneratedRelationInput[]
): NodeRelationRow[] => {
  const db = getDb();
  const now = new Date().toISOString();
  const rows = relations.map((relation) => ({
    id: `rel_${randomUUID()}`,
    from_node_id: relation.from_node_id || null,
    to_node_id: relation.to_node_id || null,
    from_card_id: relation.from_card_id || null,
    to_card_id: relation.to_card_id || null,
    relation_type: relation.relation_type,
    label: relation.label,
    source: relation.source,
    confidence: relation.confidence,
    metadata_json: relation.metadata ? JSON.stringify(relation.metadata) : null,
    created_at: now,
    updated_at: now,
    deleted_at: null
  }));

  withTransaction(() => {
    db.prepare(
      `
        UPDATE node_relations
        SET deleted_at = @now,
            updated_at = @now
        WHERE from_card_id = @card_id
          AND deleted_at IS NULL
          AND source IN (@source_0, @source_1)
      `
    ).run({
      now,
      card_id: card.id,
      source_0: GENERATED_SOURCES[0],
      source_1: GENERATED_SOURCES[1]
    });

    const insert = db.prepare(
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
          updated_at,
          deleted_at
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
          @updated_at,
          @deleted_at
        )
      `
    );

    rows.forEach((row) => insert.run(row));

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
      card_id: card.id,
      node_id: card.node_id,
      actor: 'system',
      action: 'rebuild_graph',
      before_snapshot: null,
      after_snapshot: JSON.stringify({
        relation_count: rows.length,
        source: 'card_mapping'
      }),
      created_at: now
    });
  });

  return rows as NodeRelationRow[];
};

export const rebuildGraphForCard = (cardId: string) => {
  const card = getCardOrThrow(cardId);
  getNodeOrThrow(card.node_id);

  const raw = parseJsonObject(card.raw_json);
  const knowledgeBlocks = loadKnowledgeBlocks(card.id);
  const cardItems = loadCardItems(card.id);
  const commentSignals = loadCommentSignals(card.id);
  const sourceMaterials = loadSourceMaterials(card.id);
  const relations: GeneratedRelationInput[] = [
    {
      from_card_id: card.id,
      to_node_id: card.node_id,
      relation_type: 'MOUNTED_ON',
      label: relationLabels.MOUNTED_ON,
      source: 'card_mapping',
      confidence: 1,
      metadata: {
        source_field: 'cards.node_id'
      }
    }
  ];
  const seen = new Set<string>([`MOUNTED_ON:node:${card.node_id}`]);

  const concepts = uniqueTexts([
    ...knowledgeBlocks.map((block) => block.concept),
    ...textArrayFromField(raw, 'knowledge_blocks', ['concept']),
    ...textArrayFromField(raw, 'concepts', ['concept', 'title', 'name'])
  ]);
  concepts.forEach((concept) =>
    addVirtualGeneratedRelation(
      relations,
      seen,
      card.id,
      'HAS_CONCEPT',
      'concept',
      concept,
      'knowledge_blocks[].concept'
    )
  );

  uniqueTexts([...textArrayFromField(raw, 'tags', ['tag', 'label', 'name']), ...itemTexts(cardItems, ['tag'])]).forEach(
    (tag) =>
      addVirtualGeneratedRelation(relations, seen, card.id, 'TAGGED_AS', 'tag', tag, 'tags[]')
  );

  uniqueTexts([
    ...textArrayFromField(raw, 'risks', ['risk', 'title', 'content', 'text']),
    ...itemTexts(cardItems, ['risk'])
  ]).forEach((risk) =>
    addVirtualGeneratedRelation(relations, seen, card.id, 'HAS_RISK', 'risk', risk, 'risks[]')
  );

  uniqueTexts([
    ...textArrayFromField(raw, 'follow_up_actions', ['action', 'title', 'content', 'text']),
    ...itemTexts(cardItems, ['follow_up_action', 'action'])
  ]).forEach((action) =>
    addVirtualGeneratedRelation(
      relations,
      seen,
      card.id,
      'SUGGESTS_ACTION',
      'action',
      action,
      'follow_up_actions[]'
    )
  );

  uniqueTexts([
    ...textArrayFromField(raw, 'methodology', ['method', 'title', 'content', 'text']),
    ...itemTexts(cardItems, ['methodology', 'method'])
  ]).forEach((method) =>
    addVirtualGeneratedRelation(relations, seen, card.id, 'HAS_METHOD', 'method', method, 'methodology[]')
  );

  sourceCandidates(card, raw, sourceMaterials, commentSignals).forEach((source) =>
    addVirtualGeneratedRelation(
      relations,
      seen,
      card.id,
      'DERIVED_FROM_SOURCE',
      'source',
      source.label,
      'source_title/source_url/comments_*',
      source.metadata
    )
  );

  const model = modelCandidate(card, raw);
  if (model) {
    addVirtualGeneratedRelation(
      relations,
      seen,
      card.id,
      'GENERATED_BY_MODEL',
      'model',
      model.label,
      'model_provider/model_used',
      model.metadata
    );
  }

  const inserted = insertGeneratedRelations(card, relations);
  return {
    ok: true,
    card_id: card.id,
    relation_count: inserted.length,
    relations: inserted
  };
};
