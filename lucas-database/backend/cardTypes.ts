export interface CardRow {
  id: string;
  node_id: string;
  schema_name: string;
  schema_version: string;
  card_type: string | null;
  content_level: string | null;
  quality_level: string | null;
  source_title: string | null;
  display_title: string;
  safe_filename_title: string | null;
  one_sentence_summary: string | null;
  original_summary: string | null;
  idempotency_key: string | null;
  raw_json: string;
  composer_status: string | null;
  composer_error: string | null;
  model_used: string | null;
  model_provider: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface CardKnowledgeBlockRow {
  id: string;
  card_id: string;
  concept: string;
  explanation: string | null;
  evidence: string | null;
  reusable_value: string | null;
  sort_order: number;
  created_at: string;
}

export interface CardItemRow {
  id: string;
  card_id: string;
  item_type: string;
  content: string;
  sort_order: number;
  created_at: string;
}

export interface CardCommentSignalRow {
  id: string;
  card_id: string;
  comments_hash: string | null;
  demand_or_resource_requests: string | null;
  doubts_or_objections: string | null;
  implementation_barriers: string | null;
  resonance_or_agreement: string | null;
  incremental_value: string | null;
  comments_job_id: string | null;
  comments_video_id: string | null;
  created_at: string;
}

export interface CardSourceMaterialRow {
  id: string;
  card_id: string;
  source_url: string | null;
  source_title: string | null;
  source_type: string | null;
  raw_text: string | null;
  transcript: string | null;
  ocr_text: string | null;
  comments_json: string | null;
  metadata_json: string | null;
  created_at: string;
}

export interface CardQualityGateRow {
  id: string;
  card_id: string;
  passed: number;
  quality_level: string | null;
  gate_name: string | null;
  gate_version: string | null;
  errors_json: string | null;
  warnings_json: string | null;
  raw_json: string;
  created_at: string;
}

export interface CardRenderedViewRow {
  id: string;
  card_id: string;
  view_type: string;
  content: string;
  renderer_name: string | null;
  renderer_version: string | null;
  created_at: string;
  updated_at: string;
}

export interface NodeRelationRow {
  id: string;
  from_node_id: string | null;
  to_node_id: string | null;
  from_card_id: string | null;
  to_card_id: string | null;
  relation_type: string;
  label: string | null;
  source: string;
  confidence: number;
  metadata_json: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface CardDetail {
  ok: true;
  card: CardRow & { raw_json_parsed: unknown };
  knowledge_blocks: CardKnowledgeBlockRow[];
  items: CardItemRow[];
  comment_signals: CardCommentSignalRow[];
  source_materials: CardSourceMaterialRow[];
  quality_gates: CardQualityGateRow[];
  rendered_views: CardRenderedViewRow[];
  relations: NodeRelationRow[];
}

export interface GraphNode {
  id: string;
  entity_type: string;
  label: string;
  source_id?: string;
  metadata?: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  from: string;
  to: string;
  relation_type: string;
  label: string | null;
  source: string;
  confidence: number;
  metadata?: Record<string, unknown>;
}

export interface GraphData {
  ok: true;
  nodes: GraphNode[];
  edges: GraphEdge[];
}
