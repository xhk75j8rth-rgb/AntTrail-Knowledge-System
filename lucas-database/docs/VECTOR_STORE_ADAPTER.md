# VECTOR_STORE_ADAPTER.md / 向量库存储适配说明

## 当前 Store

后端通过 `VectorStore` 接口接入向量存储，当前支持两个 store：

- `mock` / 默认值：
  - `vector_store = mock`
  - 只写入 `embedding_vectors` 引用记录。
  - `metadata_json.embedding_preview` 保存前 8 维预览，便于调试。
- `sqlite-vec` / 可选真实向量存储：
  - `vector_store = sqlite-vec`
  - 通过 NPM 包 `sqlite-vec` 加载 sqlite 扩展。
  - 按维度创建 vec0 虚拟表：`lucas_vec_embeddings_<dim>`，例如 `lucas_vec_embeddings_1024`。
  - `embedding_vectors.metadata_json` 保存 `sqlite_vec_table`、`sqlite_vec_rowid` 和 8 维 preview。

启用 sqlite-vec：

```powershell
$env:LUCAS_VECTOR_STORE='sqlite-vec'
npm run start:api
```

常见组合：

```powershell
$env:LUCAS_EMBEDDING_PROVIDER='bge-m3'
$env:LUCAS_VECTOR_STORE='sqlite-vec'
$env:LUCAS_BGE_M3_PYTHON='.\.venv-bge-m3\Scripts\python.exe'
npm run start:api
```

这里的 `.\.venv-bge-m3` 按本项目 `lucas-database` 根目录解析。

## 接口

后端接口位于 `backend/indexing/vectorStore.ts`：

```ts
export interface VectorStore {
  vectorStore: string;
  upsertBatch(records: VectorStoreUpsertInput[]): Promise<VectorStoreUpsertResult[]>;
  search(input: VectorStoreSearchInput): Promise<VectorStoreSearchResult[]>;
}
```

`upsertBatch` 接收 `chunkId + textHash + embedding + embeddingModel`，返回 `vectorId` 和维度信息。
`search` 接收 query embedding，返回 `chunkId + distance` 和必要 vector 引用信息。

`backend/indexing/vectorStoreFactory.ts` 根据 `LUCAS_VECTOR_STORE` 创建 store。

## sqlite-vec 写入规则

- `backend/db.ts` 只有在 `LUCAS_VECTOR_STORE=sqlite-vec` 时用 `allowExtension: true` 打开数据库。
- `SqliteVecStore` 首次写入时加载 sqlite-vec 扩展，随后立即关闭 extension loading。
- vec0 表名按维度隔离：`lucas_vec_embeddings_8`、`lucas_vec_embeddings_1024` 等。
- vec0 `rowid` 由稳定 `vector_id` 哈希生成，重复 rebuild 同一 chunk 会覆盖同一 row。
- `embedding_vectors` 仍然是 Lucas 侧的引用表，不把真实向量当主数据。

## 检索流程

`backend/indexing/retrievalService.ts` 已实现 Vector Retrieval V0：

- 用 provider 生成 query embedding。
- 查询对应维度的 `lucas_vec_embeddings_<dim>`。
- sqlite-vec 返回 rowid / distance 后，经 `embedding_vectors.metadata_json.sqlite_vec_rowid` 回到 `chunk_id`。
- RetrievalService 再回 `chunks`、`nodes`、`cards` 补全 chunk、node、card、path 和 metadata。
- API：`POST /api/retrieval/vector`。

不要把 Node / Card 正文复制成向量库里的权威 payload。
