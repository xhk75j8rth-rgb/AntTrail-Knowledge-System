import { createHash } from 'node:crypto';
import * as sqliteVec from 'sqlite-vec';
import { getDb } from '../db';
import { makeStableId } from './chunker';
import { listActiveVectorsByStore, upsertEmbeddingVector } from './embeddingVectorRepo';
import type {
  VectorStore,
  VectorStoreSearchInput,
  VectorStoreSearchResult,
  VectorStoreUpsertInput,
  VectorStoreUpsertResult
} from './vectorStore';

let extensionLoaded = false;

const toFloat32Blob = (embedding: number[]) => {
  const float32 = Float32Array.from(embedding);
  return new Uint8Array(float32.buffer);
};

const embeddingPreview = (embedding: number[]) => embedding.slice(0, 8);

const parseMetadataJson = (value: string | null) => {
  if (!value) {
    return {};
  }
  try {
    const parsed = JSON.parse(value);
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
};

const vectorTableName = (embeddingDim: number) => {
  if (!Number.isInteger(embeddingDim) || embeddingDim <= 0 || embeddingDim > 16384) {
    throw new Error(`Invalid sqlite-vec embedding dimension: ${embeddingDim}`);
  }
  return `lucas_vec_embeddings_${embeddingDim}`;
};

const vectorRowId = (vectorId: string) => {
  const hex = createHash('sha256').update(vectorId, 'utf8').digest('hex').slice(0, 15);
  const rowId = BigInt(`0x${hex}`);
  return rowId === 0n ? 1n : rowId;
};

const ensureSqliteVecLoaded = () => {
  if (extensionLoaded) {
    return;
  }

  const db = getDb();
  sqliteVec.load(db);
  db.enableLoadExtension(false);
  extensionLoaded = true;
};

const ensureVectorTable = (embeddingDim: number) => {
  ensureSqliteVecLoaded();
  const tableName = vectorTableName(embeddingDim);
  getDb().exec(`CREATE VIRTUAL TABLE IF NOT EXISTS ${tableName} USING vec0(embedding float[${embeddingDim}])`);
  return tableName;
};

export class SqliteVecStore implements VectorStore {
  vectorStore = 'sqlite-vec';

  async upsertBatch(records: VectorStoreUpsertInput[]): Promise<VectorStoreUpsertResult[]> {
    if (records.length === 0) {
      return [];
    }

    const now = new Date().toISOString();
    const results: VectorStoreUpsertResult[] = [];
    const tableCache = new Map<number, string>();
    const db = getDb();

    for (const record of records) {
      const embeddingDim = record.embedding.length;
      const tableName = tableCache.get(embeddingDim) || ensureVectorTable(embeddingDim);
      tableCache.set(embeddingDim, tableName);

      const vectorId = makeStableId('sqlite_vec_vector', [
        record.chunkId,
        record.textHash,
        record.embeddingModel
      ]);
      const rowId = vectorRowId(vectorId);
      const id = makeStableId('emb_vec', [this.vectorStore, vectorId]);
      const preview = embeddingPreview(record.embedding);

      db.prepare(`DELETE FROM ${tableName} WHERE rowid = ?`).run(rowId);
      db.prepare(`INSERT INTO ${tableName}(rowid, embedding) VALUES (?, ?)`).run(
        rowId,
        toFloat32Blob(record.embedding)
      );

      upsertEmbeddingVector(
        {
          id,
          chunkId: record.chunkId,
          vectorStore: this.vectorStore,
          vectorId,
          embeddingModel: record.embeddingModel,
          embeddingDim,
          textHash: record.textHash,
          metadata: {
            ...(record.metadata || {}),
            sqlite_vec_table: tableName,
            sqlite_vec_rowid: rowId.toString(),
            embedding_preview: preview,
            embedding_preview_dim: preview.length
          }
        },
        now
      );

      results.push({
        chunkId: record.chunkId,
        vectorStore: this.vectorStore,
        vectorId,
        embeddingModel: record.embeddingModel,
        embeddingDim
      });
    }

    return results;
  }

  async search(input: VectorStoreSearchInput): Promise<VectorStoreSearchResult[]> {
    if (input.embedding.length === 0) {
      return [];
    }

    const embeddingDim = input.embedding.length;
    const tableName = ensureVectorTable(embeddingDim);
    const vectorRows = listActiveVectorsByStore({
      vectorStore: this.vectorStore,
      embeddingModel: input.embeddingModel,
      embeddingDim,
      limit: 50000
    });
    const vectorsByRowId = new Map(
      vectorRows
        .map((row) => {
          const metadata = parseMetadataJson(row.metadata_json);
          const rowId = typeof metadata.sqlite_vec_rowid === 'string' ? metadata.sqlite_vec_rowid : '';
          return rowId ? [rowId, { row, metadata }] : null;
        })
        .filter((entry): entry is [string, { row: (typeof vectorRows)[number]; metadata: Record<string, unknown> }] =>
          Boolean(entry)
        )
    );

    if (vectorsByRowId.size === 0) {
      return [];
    }

    const requestedK = Math.max(input.limit, Math.min(input.limit * 5, 200));
    const statement = getDb().prepare(
      `
        SELECT rowid, distance
        FROM ${tableName}
        WHERE embedding MATCH ?
          AND k = ?
        ORDER BY distance
      `
    );
    statement.setReadBigInts(true);
    const rows = statement.all(toFloat32Blob(input.embedding), BigInt(requestedK)) as unknown as Array<{
      rowid: number | bigint;
      distance: number;
    }>;

    const results: VectorStoreSearchResult[] = [];
    for (const row of rows) {
      const vector = vectorsByRowId.get(String(row.rowid));
      if (!vector) {
        continue;
      }

      results.push({
        chunkId: vector.row.chunk_id,
        vectorStore: vector.row.vector_store,
        vectorId: vector.row.vector_id,
        embeddingModel: vector.row.embedding_model,
        embeddingDim: vector.row.embedding_dim,
        distance: row.distance,
        metadata: vector.metadata
      });

      if (results.length >= input.limit) {
        break;
      }
    }

    return results;
  }
}
