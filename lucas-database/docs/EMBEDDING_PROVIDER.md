# EMBEDDING_PROVIDER.md / 向量模型接入说明

## 当前 Provider

后端通过 `EmbeddingProvider` 接口接入向量模型，当前支持两个 provider：

- `mock` / 默认值：
  - `embedding_model = mock-embedding-v0`
  - `embedding_dim = 8`
  - 用于本地开发、测试和无 Python/CUDA 环境的稳定启动。
- `bge-m3` / 可选真实 provider：
  - `embedding_model = BAAI/bge-m3`
  - `embedding_dim = 1024`
  - 通过长驻 Python worker 调用本项目 `lucas-database/.venv-bge-m3` 中的 `SentenceTransformer`。

启用 BGE-M3：

```powershell
$env:LUCAS_EMBEDDING_PROVIDER='bge-m3'
$env:LUCAS_BGE_M3_PYTHON='.\.venv-bge-m3\Scripts\python.exe'
$env:BGE_M3_MODEL_DIR='<local-huggingface-cache>\models--BAAI--bge-m3\snapshots\<snapshot-id>'
npm run start:api
```

可选环境变量：

- `LUCAS_EMBEDDING_PROVIDER`：`mock` 或 `bge-m3`，默认 `mock`。
- `LUCAS_BGE_M3_PYTHON`：Python 可执行文件路径，默认 `lucas-database/.venv-bge-m3`；相对路径按本项目 `lucas-database` 根目录解析。
- `LUCAS_BGE_M3_WORKER`：worker 脚本路径，默认 `backend/indexing/bge_m3_worker.py`；相对路径按本项目 `lucas-database` 根目录解析。
- `LUCAS_BGE_M3_MODEL`：模型名，默认 `BAAI/bge-m3`。
- `BGE_M3_MODEL_DIR`：本地模型 snapshot 路径；不设置时 worker 会查 Hugging Face cache。
- `LUCAS_BGE_M3_BATCH_SIZE`：默认 `12`。
- `LUCAS_BGE_M3_TIMEOUT_MS`：默认 `120000`。

## 接口

后端接口位于 `backend/indexing/embeddingProvider.ts`：

```ts
export interface EmbeddingProvider {
  embeddingModel: string;
  embeddingDim: number;
  embedBatch(texts: string[]): Promise<EmbeddingResult[]>;
}
```

`backend/indexing/embeddingProviderFactory.ts` 根据 `LUCAS_EMBEDDING_PROVIDER` 创建 provider。

## BGE-M3 Worker

BGE-M3 provider 的 TypeScript 入口是 `backend/indexing/bgeM3EmbeddingProvider.ts`，Python worker 是 `backend/indexing/bge_m3_worker.py`。

worker 使用 JSON Lines 协议：

- Node 发送 `{ id, type: "embed", model, texts, batch_size }`。
- Python 返回 `{ id, ok, model, embedding_dim, embeddings }`。
- 首次请求时加载模型；后续请求复用同一个 worker 进程和模型实例。
- worker 优先使用 CUDA；如果 `torch.cuda.is_available()` 为 false，会回退 CPU，但正式环境应显式关注性能风险。

## 写入规则

- 不改变 `chunks` 表。
- `embedding_jobs.embedding_model` 写实际 provider 模型名。
- `embedding_vectors.embedding_model` 和 `embedding_dim` 写实际模型信息。
- `embedding_vectors.vector_store` 由 `LUCAS_VECTOR_STORE` 决定；可为默认 `mock`，也可为 `sqlite-vec`。
- 失败时 `embedding_jobs.status = failed` 并保存错误。

不要在 provider 里写主数据。provider 只负责文本到向量。
