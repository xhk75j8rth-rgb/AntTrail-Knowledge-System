export interface ChunkInput {
  targetType: 'node' | 'card' | 'source_material' | string;
  targetId: string;
  title?: string;
  content?: string;
  rawJson?: unknown;
  metadata?: Record<string, unknown>;
}

export interface ChunkOutput {
  id?: string;
  targetType: string;
  targetId: string;
  sourceTable?: string;
  chunkType: string;
  chunkText: string;
  chunkIndex: number;
  textHash: string;
  tokenCount?: number;
  metadata?: Record<string, unknown>;
}

export interface Chunker {
  chunk(input: ChunkInput): ChunkOutput[];
}

export interface ChunkRow {
  id: string;
  target_type: string;
  target_id: string;
  source_table: string | null;
  chunk_type: string;
  chunk_text: string;
  chunk_index: number;
  text_hash: string;
  token_count: number | null;
  metadata_json: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}
