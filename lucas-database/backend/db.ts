import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs';
import path from 'node:path';

export const DATA_DIR = process.env.LUCAS_DB_DATA_DIR
  ? path.resolve(process.env.LUCAS_DB_DATA_DIR)
  : path.resolve(process.cwd(), 'data');

export const DB_PATH = process.env.LUCAS_DB_PATH
  ? path.resolve(process.env.LUCAS_DB_PATH)
  : path.join(DATA_DIR, 'lucas.db');

export const ATTACHMENTS_DIR = process.env.LUCAS_DB_ATTACHMENTS_DIR
  ? path.resolve(process.env.LUCAS_DB_ATTACHMENTS_DIR)
  : path.join(DATA_DIR, 'attachments');

let db: DatabaseSync | null = null;

const shouldAllowLoadableExtensions = () => {
  const vectorStore = (process.env.LUCAS_VECTOR_STORE || 'mock').trim().toLowerCase();
  return vectorStore === 'sqlite-vec' || vectorStore === 'sqlite_vec' || vectorStore === 'sqlitevec';
};

export const getDb = () => {
  if (!db) {
    fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
    db = new DatabaseSync(DB_PATH, {
      allowExtension: shouldAllowLoadableExtensions()
    });
    db.exec('PRAGMA journal_mode = WAL');
    db.exec('PRAGMA foreign_keys = ON');
    migrate(db);
  }

  return db;
};

export const withTransaction = <T>(work: () => T): T => {
  const database = getDb();
  database.exec('BEGIN IMMEDIATE');
  try {
    const result = work();
    database.exec('COMMIT');
    return result;
  } catch (error) {
    database.exec('ROLLBACK');
    throw error;
  }
};

const migrate = (database: DatabaseSync) => {
  database.exec(`
    CREATE TABLE IF NOT EXISTS nodes (
      id TEXT PRIMARY KEY,
      parent_id TEXT,
      title TEXT NOT NULL,
      slug TEXT,
      type TEXT NOT NULL DEFAULT 'page',
      content TEXT NOT NULL DEFAULT '',
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      deleted_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_nodes_parent_id ON nodes(parent_id);
    CREATE INDEX IF NOT EXISTS idx_nodes_deleted_at ON nodes(deleted_at);
    CREATE INDEX IF NOT EXISTS idx_nodes_sort ON nodes(parent_id, sort_order, created_at);

    CREATE TABLE IF NOT EXISTS node_events (
      id TEXT PRIMARY KEY,
      node_id TEXT,
      actor TEXT NOT NULL DEFAULT 'user',
      action TEXT NOT NULL,
      before_snapshot TEXT,
      after_snapshot TEXT,
      created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_node_events_node_id ON node_events(node_id);
    CREATE INDEX IF NOT EXISTS idx_node_events_created_at ON node_events(created_at);

    CREATE TABLE IF NOT EXISTS node_attachments (
      id TEXT PRIMARY KEY,
      node_id TEXT NOT NULL,
      original_name TEXT NOT NULL,
      stored_name TEXT NOT NULL,
      relative_path TEXT NOT NULL,
      mime_type TEXT NOT NULL,
      size_bytes INTEGER NOT NULL,
      kind TEXT NOT NULL,
      created_at TEXT NOT NULL,
      deleted_at TEXT,
      FOREIGN KEY (node_id) REFERENCES nodes(id)
    );

    CREATE INDEX IF NOT EXISTS idx_node_attachments_node_id
      ON node_attachments(node_id);
    CREATE INDEX IF NOT EXISTS idx_node_attachments_deleted_at
      ON node_attachments(deleted_at);

    CREATE TABLE IF NOT EXISTS api_tokens (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      token_hash TEXT NOT NULL,
      token_preview TEXT NOT NULL DEFAULT '',
      scopes TEXT NOT NULL,
      created_at TEXT NOT NULL,
      last_used_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_api_tokens_created_at ON api_tokens(created_at);

    CREATE TABLE IF NOT EXISTS cards (
      id TEXT PRIMARY KEY,
      node_id TEXT NOT NULL,
      schema_name TEXT NOT NULL,
      schema_version TEXT NOT NULL,
      card_type TEXT,
      content_level TEXT,
      quality_level TEXT,
      source_title TEXT,
      display_title TEXT NOT NULL,
      safe_filename_title TEXT,
      one_sentence_summary TEXT,
      original_summary TEXT,
      idempotency_key TEXT,
      raw_json TEXT NOT NULL,
      composer_status TEXT,
      composer_error TEXT,
      model_used TEXT,
      model_provider TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      deleted_at TEXT,
      FOREIGN KEY (node_id) REFERENCES nodes(id)
    );

    CREATE INDEX IF NOT EXISTS idx_cards_node_id ON cards(node_id);
    CREATE INDEX IF NOT EXISTS idx_cards_schema ON cards(schema_name, schema_version);
    CREATE INDEX IF NOT EXISTS idx_cards_deleted_at ON cards(deleted_at);

    CREATE TABLE IF NOT EXISTS card_knowledge_blocks (
      id TEXT PRIMARY KEY,
      card_id TEXT NOT NULL,
      concept TEXT NOT NULL,
      explanation TEXT,
      evidence TEXT,
      reusable_value TEXT,
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      FOREIGN KEY (card_id) REFERENCES cards(id)
    );

    CREATE INDEX IF NOT EXISTS idx_card_knowledge_blocks_card_id
      ON card_knowledge_blocks(card_id);

    CREATE TABLE IF NOT EXISTS card_items (
      id TEXT PRIMARY KEY,
      card_id TEXT NOT NULL,
      item_type TEXT NOT NULL,
      content TEXT NOT NULL,
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      FOREIGN KEY (card_id) REFERENCES cards(id)
    );

    CREATE INDEX IF NOT EXISTS idx_card_items_card_id ON card_items(card_id);
    CREATE INDEX IF NOT EXISTS idx_card_items_type ON card_items(item_type);

    CREATE TABLE IF NOT EXISTS card_comment_signals (
      id TEXT PRIMARY KEY,
      card_id TEXT NOT NULL,
      comments_hash TEXT,
      demand_or_resource_requests TEXT,
      doubts_or_objections TEXT,
      implementation_barriers TEXT,
      resonance_or_agreement TEXT,
      incremental_value TEXT,
      comments_job_id TEXT,
      comments_video_id TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY (card_id) REFERENCES cards(id)
    );

    CREATE INDEX IF NOT EXISTS idx_card_comment_signals_card_id
      ON card_comment_signals(card_id);

    CREATE TABLE IF NOT EXISTS card_source_materials (
      id TEXT PRIMARY KEY,
      card_id TEXT NOT NULL,
      source_url TEXT,
      source_title TEXT,
      source_type TEXT,
      raw_text TEXT,
      transcript TEXT,
      ocr_text TEXT,
      comments_json TEXT,
      metadata_json TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY (card_id) REFERENCES cards(id)
    );

    CREATE INDEX IF NOT EXISTS idx_card_source_materials_card_id
      ON card_source_materials(card_id);
    CREATE INDEX IF NOT EXISTS idx_card_source_materials_source_url
      ON card_source_materials(source_url);

    CREATE TABLE IF NOT EXISTS card_quality_gates (
      id TEXT PRIMARY KEY,
      card_id TEXT NOT NULL,
      passed INTEGER NOT NULL DEFAULT 0,
      quality_level TEXT,
      gate_name TEXT,
      gate_version TEXT,
      errors_json TEXT,
      warnings_json TEXT,
      raw_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY (card_id) REFERENCES cards(id)
    );

    CREATE INDEX IF NOT EXISTS idx_card_quality_gates_card_id
      ON card_quality_gates(card_id);

    CREATE TABLE IF NOT EXISTS card_rendered_views (
      id TEXT PRIMARY KEY,
      card_id TEXT NOT NULL,
      view_type TEXT NOT NULL,
      content TEXT NOT NULL,
      renderer_name TEXT,
      renderer_version TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      FOREIGN KEY (card_id) REFERENCES cards(id)
    );

    CREATE INDEX IF NOT EXISTS idx_card_rendered_views_card_id
      ON card_rendered_views(card_id);
    CREATE INDEX IF NOT EXISTS idx_card_rendered_views_type
      ON card_rendered_views(view_type);

    CREATE TABLE IF NOT EXISTS node_relations (
      id TEXT PRIMARY KEY,
      from_node_id TEXT,
      to_node_id TEXT,
      from_card_id TEXT,
      to_card_id TEXT,
      relation_type TEXT NOT NULL,
      label TEXT,
      source TEXT NOT NULL DEFAULT 'system_rule',
      confidence REAL NOT NULL DEFAULT 1,
      metadata_json TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      deleted_at TEXT,
      FOREIGN KEY (from_node_id) REFERENCES nodes(id),
      FOREIGN KEY (to_node_id) REFERENCES nodes(id),
      FOREIGN KEY (from_card_id) REFERENCES cards(id),
      FOREIGN KEY (to_card_id) REFERENCES cards(id)
    );

    CREATE INDEX IF NOT EXISTS idx_node_relations_from_node
      ON node_relations(from_node_id);
    CREATE INDEX IF NOT EXISTS idx_node_relations_to_node
      ON node_relations(to_node_id);
    CREATE INDEX IF NOT EXISTS idx_node_relations_from_card
      ON node_relations(from_card_id);
    CREATE INDEX IF NOT EXISTS idx_node_relations_to_card
      ON node_relations(to_card_id);
    CREATE INDEX IF NOT EXISTS idx_node_relations_type
      ON node_relations(relation_type);
    CREATE INDEX IF NOT EXISTS idx_node_relations_deleted_at
      ON node_relations(deleted_at);

    CREATE TABLE IF NOT EXISTS card_events (
      id TEXT PRIMARY KEY,
      card_id TEXT,
      node_id TEXT,
      actor TEXT NOT NULL DEFAULT 'agent',
      action TEXT NOT NULL,
      before_snapshot TEXT,
      after_snapshot TEXT,
      created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_card_events_card_id ON card_events(card_id);
    CREATE INDEX IF NOT EXISTS idx_card_events_node_id ON card_events(node_id);
    CREATE INDEX IF NOT EXISTS idx_card_events_created_at ON card_events(created_at);

    CREATE TABLE IF NOT EXISTS chunks (
      id TEXT PRIMARY KEY,
      target_type TEXT NOT NULL,
      target_id TEXT NOT NULL,
      source_table TEXT,
      chunk_type TEXT NOT NULL,
      chunk_text TEXT NOT NULL,
      chunk_index INTEGER NOT NULL DEFAULT 0,
      text_hash TEXT NOT NULL,
      token_count INTEGER DEFAULT 0,
      metadata_json TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      deleted_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_chunks_target ON chunks(target_type, target_id);
    CREATE INDEX IF NOT EXISTS idx_chunks_text_hash ON chunks(text_hash);
    CREATE INDEX IF NOT EXISTS idx_chunks_deleted_at ON chunks(deleted_at);

    CREATE TABLE IF NOT EXISTS embedding_jobs (
      id TEXT PRIMARY KEY,
      target_type TEXT NOT NULL,
      target_id TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',
      embedding_model TEXT NOT NULL DEFAULT 'mock-embedding-v0',
      chunk_count INTEGER DEFAULT 0,
      error TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      completed_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_embedding_jobs_target
      ON embedding_jobs(target_type, target_id);
    CREATE INDEX IF NOT EXISTS idx_embedding_jobs_status
      ON embedding_jobs(status);

    CREATE TABLE IF NOT EXISTS embedding_vectors (
      id TEXT PRIMARY KEY,
      chunk_id TEXT NOT NULL,
      vector_store TEXT NOT NULL DEFAULT 'mock',
      vector_id TEXT NOT NULL,
      embedding_model TEXT NOT NULL DEFAULT 'mock-embedding-v0',
      embedding_dim INTEGER NOT NULL DEFAULT 8,
      text_hash TEXT NOT NULL,
      metadata_json TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      deleted_at TEXT,
      FOREIGN KEY (chunk_id) REFERENCES chunks(id)
    );

    CREATE INDEX IF NOT EXISTS idx_embedding_vectors_chunk
      ON embedding_vectors(chunk_id);
    CREATE INDEX IF NOT EXISTS idx_embedding_vectors_text_hash
      ON embedding_vectors(text_hash);
  `);

  if (!hasColumn(database, 'api_tokens', 'token_preview')) {
    database.exec(`ALTER TABLE api_tokens ADD COLUMN token_preview TEXT NOT NULL DEFAULT ''`);
  }

  if (!hasColumn(database, 'node_relations', 'deleted_at')) {
    database.exec(`ALTER TABLE node_relations ADD COLUMN deleted_at TEXT`);
  }

  if (!hasColumn(database, 'cards', 'idempotency_key')) {
    database.exec(`ALTER TABLE cards ADD COLUMN idempotency_key TEXT`);
  }

  database.exec(`
    CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_idempotency_key
      ON cards(idempotency_key)
      WHERE idempotency_key IS NOT NULL;
  `);

  syncGeneratedCardNodeContent(database);
};

const syncGeneratedCardNodeContent = (database: DatabaseSync) => {
  database
    .prepare(
      `
        UPDATE nodes
        SET content = (
              SELECT plain.content
              FROM cards AS c
              JOIN card_rendered_views AS markdown
                ON markdown.card_id = c.id
               AND markdown.view_type = 'markdown'
              JOIN card_rendered_views AS plain
                ON plain.card_id = c.id
               AND plain.view_type = 'plain_text'
              WHERE c.node_id = nodes.id
                AND c.deleted_at IS NULL
                AND nodes.content = markdown.content
                AND plain.content <> markdown.content
              ORDER BY c.updated_at DESC, c.created_at DESC
              LIMIT 1
            ),
            updated_at = @updated_at
        WHERE deleted_at IS NULL
          AND EXISTS (
            SELECT 1
            FROM cards AS c
            JOIN card_rendered_views AS markdown
              ON markdown.card_id = c.id
             AND markdown.view_type = 'markdown'
            JOIN card_rendered_views AS plain
              ON plain.card_id = c.id
             AND plain.view_type = 'plain_text'
            WHERE c.node_id = nodes.id
              AND c.deleted_at IS NULL
              AND nodes.content = markdown.content
              AND plain.content <> markdown.content
          )
      `
    )
    .run({ updated_at: new Date().toISOString() });
};

const hasColumn = (database: DatabaseSync, tableName: string, columnName: string) => {
  const columns = database.prepare(`PRAGMA table_info(${tableName})`).all() as Array<{
    name: string;
  }>;
  return columns.some((column) => column.name === columnName);
};
