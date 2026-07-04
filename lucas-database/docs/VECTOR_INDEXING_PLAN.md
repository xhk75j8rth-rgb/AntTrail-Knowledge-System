# VECTOR_INDEXING_PLAN.md / 向量索引计划

## 定位

Vector Indexing V0 只建立向量索引底座，不上线真实语义搜索。Lucas Database 仍是主数据库，Node、Card、SourceMaterial 等主数据才是权威来源。

向量索引是派生索引：

- `chunks` 可从 Node / Card 重新切块生成。
- `embedding_jobs` 记录每次重建任务。
- `embedding_vectors` 记录 chunk 到向量存储的引用。
- 后续真实向量库只应返回 `chunk_id + score`，命中后仍回 Lucas Database 读取权威正文和结构化卡片。

## 本阶段实现

- `POST /api/indexing/rebuild-node/:id`：读取 Node 的 `title/content`，用 `MarkdownChunker` 生成 chunks。
- `POST /api/indexing/rebuild-card/:id`：读取 `cards.raw_json`，用 `CardAwareChunker` 生成 chunks。
- `POST /api/indexing/rebuild-all`：批量重建 active Node / Card 的 chunks 和向量索引。
- `GET /api/indexing/provider`：查看当前配置的 embedding provider，不触发模型加载。
- `GET /api/indexing/jobs`：查询索引任务。
- `GET /api/indexing/chunks?target_type=&target_id=`：查询目标活跃 chunks。
- `GET /api/indexing/chunks/:id/vector`：查询 chunk 的向量引用。
- `POST /api/retrieval/vector`：使用当前 provider 生成 query embedding，调用当前 vector store 检索，并补全 chunk / node / card / path / metadata。
- `POST /api/retrieval/keyword`：使用关键词包含匹配检索 active chunks、Node 标题和 Card 标题，并返回同样的 hydration 结构。
- `POST /api/retrieval/hybrid`：同时执行 vector 和 keyword 检索，按 `chunk_id` 融合并返回可解释 signals。
- `POST /api/context/build`：基于 Hybrid Search 结果去重、分组、保留 sources/signals，并按 token budget 输出 `context_text`。

## 表职责

- `chunks`：保存文本切块、稳定 `text_hash`、顺序和元数据。
- `embedding_jobs`：保存 rebuild 任务状态、模型名、chunk 数和错误信息。
- `embedding_vectors`：保存向量存储引用。默认 `vector_store = mock`；设置 `LUCAS_VECTOR_STORE=sqlite-vec` 后会写 `vector_store = sqlite-vec`，并把真实向量写入 sqlite-vec vec0 表。

## 重建策略

rebuild 流程会先创建 `embedding_jobs`，再读取主数据、生成 chunks、软删除该 target 的旧 chunks / vectors，并 upsert 新 chunks 和 vector 引用。

chunk ID 由 `target_type + target_id + chunk_type + chunk_index + text_hash` 稳定生成。mock vector ID 由 `chunk_id + text_hash + embedding_model` 稳定生成。重复执行同一份内容不会生成无限重复的活跃 chunk。

## 本阶段不做

- 不接 sqlite-vec、Qdrant、pgvector。
- 不做真实语义搜索或 Hybrid Search。
- 不把向量库 payload 当主数据。

## Provider 状态

- 默认 embedding provider 仍是 `mock`，保证没有 Python/CUDA/模型时服务可稳定启动。
- 设置 `LUCAS_EMBEDDING_PROVIDER=bge-m3` 后，rebuild 会通过 `BgeM3EmbeddingProvider` 调用长驻 Python worker，写入 `BAAI/bge-m3` 和 `1024` 维信息。
- 默认 vector store 仍是 `mock`；设置 `LUCAS_VECTOR_STORE=sqlite-vec` 后，rebuild 会把向量写入 `lucas_vec_embeddings_<dim>`。

## 下一阶段

下一阶段可以在 Context Builder V0 基础上增加 Agent Retrieve API、过滤条件和图谱扩展信号；检索命中后仍应回 Lucas Database 读取权威正文和结构化卡片。
