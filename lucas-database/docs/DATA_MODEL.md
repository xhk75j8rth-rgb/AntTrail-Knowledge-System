# DATA_MODEL.md / 数据模型说明

## 为什么用 Node / 节点模型

Lucas Database V0 不把数据设计成 Folder / 文件夹加 Document / 文档，而是统一使用 Node / 节点。这样任意节点都既是一个可编辑内容入口，也是一个可以继续承载子节点的容器。

这种模型适合个人知识库和 Agent 写作：项目、方案、日志、任务、上下文都可以在同一棵树里逐层展开，不需要提前判断某个条目到底是文件夹还是文档。

## 为什么每个节点既有内容又有子节点

真实工作资料通常不是严格的文件夹和文件关系。例如“技术方案”本身需要保存概要内容，同时下面还会继续拆分“数据库设计”“API 设计”“前端结构”。节点模型允许父节点保存总结、背景、边界，子节点保存细节。

## nodes / 节点表

```sql
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
```

字段说明：

- `id` / 节点 ID，V0 使用 `node_` 加 UUID。
- `parent_id` / 父节点 ID，根节点为 `NULL`。
- `title` / 节点标题，不能为空。
- `slug` / 路径名，由标题生成，供后续扩展使用。
- `type` / 节点类型，V0 默认 `page`。
- `content` / 节点内容视图，默认为空字符串。对结构化卡片节点，它默认使用 `plain_text` 派生视图；Markdown 只保存在 `card_rendered_views` 中作为展示出口。
- `sort_order` / 同级排序字段，V0 先预留，不做拖拽排序。
- `created_at` / 创建时间，ISO 字符串。
- `updated_at` / 更新时间，ISO 字符串。
- `deleted_at` / 软删除时间，未删除为 `NULL`。

## node_events / 节点事件表

```sql
CREATE TABLE IF NOT EXISTS node_events (
  id TEXT PRIMARY KEY,
  node_id TEXT,
  actor TEXT NOT NULL DEFAULT 'user',
  action TEXT NOT NULL,
  before_snapshot TEXT,
  after_snapshot TEXT,
  created_at TEXT NOT NULL
);
```

字段说明：

- `id` / 事件 ID，V0 使用 `event_` 加 UUID。
- `node_id` / 节点 ID。
- `actor` / 操作者，例如 `user`、`agent`、`system`。
- `action` / 动作，例如 `create`、`update`、`append`、`delete`、`move`。
- `before_snapshot` / 修改前 JSON 快照。
- `after_snapshot` / 修改后 JSON 快照。
- `created_at` / 事件创建时间。

事件记录策略：

- 创建节点写 `create`。
- 更新标题或内容视图写 `update`。
- 追加内容视图写 `append`。
- 上传附件写 `attach`。
- 删除附件写 `detach`。
- 软删除写 `delete`。
- 按路径写入时，自动创建的中间节点各写一次 `create`，最终写入节点写 `update` 或 `append`。

## node_attachments / 节点附件表

```sql
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
```

字段说明：

- `id` / 附件 ID，V0 使用 `att_` 加 UUID。
- `node_id` / 附件归属节点。
- `original_name` / 用户上传时的原始文件名，经过文件名安全清理。
- `stored_name` / 磁盘保存文件名，包含附件 ID，避免重名覆盖。
- `relative_path` / 相对于附件根目录的路径。
- `mime_type` / 上传时识别到的 MIME 类型。
- `size_bytes` / 文件大小，单位字节。
- `kind` / 预览类型，取值为 `image`、`video` 或 `file`。
- `created_at` / 上传时间。
- `deleted_at` / 软删除时间，未删除为 `NULL`。

附件文件默认保存在 `data/attachments`，可以用 `LUCAS_DB_ATTACHMENTS_DIR` 指定独立目录。SQLite 不保存文件二进制，只保存附件索引和元数据。删除附件时先软删除记录，V0 不立即物理删除磁盘文件。

## api_tokens / API Token 表

```sql
CREATE TABLE IF NOT EXISTS api_tokens (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  token_hash TEXT NOT NULL,
  token_preview TEXT NOT NULL DEFAULT '',
  scopes TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_used_at TEXT
);
```

V0 已创建表结构，并在本次补齐里接入了最小 Token 管理：设置页可读取状态并重置本地 Token。当前策略是：

- `LUCAS_DB_API_TOKEN` 优先级最高，设置后由环境变量锁定。
- 未设置环境变量时，服务端会在 SQLite 里保存一条最新 Token 的 hash。
- `token_preview` 仅用于设置页展示，不保存明文。
- 重置会删除旧的数据库 Token 并生成新的本地 Token，旧 Token 立即失效。

## 软删除策略

删除节点时不做物理删除，只设置 `deleted_at`。V0 默认连同所有未删除子节点一起软删除。读取树、读取详情、搜索接口都过滤 `deleted_at IS NULL`，所以已删除节点对普通 API 表现为不存在。

## 路径策略

`POST /api/write-by-path` 使用 `/` 分隔路径。服务端逐级查找同父节点下标题相同的节点；不存在则自动创建。最后一级节点用于覆盖或追加内容视图。

## 结构化卡片 / Structured Cards

V0 新增卡片主库能力，详见 [CARD_STORAGE_MODEL.md](CARD_STORAGE_MODEL.md)。

新增表：

- `cards` / 卡片主表，保存 `ComposedCardV1` 主信息和完整 `raw_json`。
- `card_knowledge_blocks` / 卡片知识块表。
- `card_items` / 卡片列表项表。
- `card_comment_signals` / 评论信号表。
- `card_source_materials` / 原始材料表。
- `card_quality_gates` / 质量门控结果表。
- `card_rendered_views` / 渲染视图表。
- `node_relations` / 节点关系表。
- `card_events` / 卡片事件表。

原则：

- `ComposedCardV1` 是主数据。
- Node 内容、Markdown、Plain Text、Card UI 和 Graph View 都从 Canonical Card JSON 派生。
- `raw_json` 必须完整保存。
- `schema_name` 和 `schema_version` 必须保存。
- `idempotency_key` 保存 `Idempotency-Key` 或 `source_material.metadata.job_id` 派生的去重键，并通过唯一索引避免重复入库。
- SiYuan Markdown 只属于展示或同步出口，不作为图谱主数据来源。
- `source_material.ocr_evidence_items` 中 `image_worth_saving: true` 的本机 `frame_path` 会在入库时复制到附件存储，并在 `card_source_materials.metadata_json.ocr_evidence_items` 中补充 `asset_url` 和 `attachment_id`。前端渲染优先使用 `asset_url`，`frame_path` 仅用于诊断。

## node_relations / 图谱关系表

```sql
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
  deleted_at TEXT
);
```

字段说明：

- `id` / 关系 ID，V0 使用 `rel_` 加 UUID。
- `from_node_id`、`to_node_id` / Node 端点。
- `from_card_id`、`to_card_id` / Card 端点。
- `relation_type` / 关系类型，例如 `MOUNTED_ON`、`HAS_CONCEPT`。
- `label` / 显示标签。
- `source` / 来源，例如 `card_mapping`、`system_rule`、`manual`、`agent`。
- `confidence` / 置信度，规则型关系默认 `1`。
- `metadata_json` / 虚拟实体和来源字段等扩展信息。
- `deleted_at` / 关系软删除时间，普通图谱查询只读取 `NULL`。

V0 暂不为 Concept、Tag、Risk、Action、Method、Source、Model 单独建实体表。规则型映射会把这些目标写入 `metadata_json`，并由 `GET /api/graph` 返回为虚拟图谱节点。

节点树父子结构也会进入图谱返回：`GET /api/graph` 会根据 `nodes.parent_id` 派生 `CONTAINS` 边，边 ID 形如 `tree:<parent_id>:<child_id>`，`metadata.source_field` 为 `nodes.parent_id`，`metadata.structural` 为 `true`。这类边不写入 `node_relations`，属于查询时生成的结构关系。

## 规则型图谱关系

- Card `MOUNTED_ON` Node。
- Card `HAS_CONCEPT` Concept。
- Card `TAGGED_AS` Tag。
- Card `HAS_RISK` Risk。
- Card `SUGGESTS_ACTION` Action。
- Card `HAS_METHOD` Method。
- Card `DERIVED_FROM_SOURCE` Source。
- Card `GENERATED_BY_MODEL` Model。
- Node `CONTAINS` Node，由 `nodes.parent_id` 派生。

`POST /api/cards/:id/rebuild-graph` 只重建来源为 `card_mapping` 或 `system_rule` 的关系，不覆盖 `manual` 和 `agent` 关系。

## 向量索引派生表

Vector Indexing V0 新增三张派生表。它们不是主数据，可以从 Node / Card 重新生成。

### chunks / 文本切块表

```sql
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
```

`target_type + target_id` 指向主数据，`text_hash` 用于判断文本稳定性，`metadata_json` 保存字段来源或 Markdown 标题信息。

### embedding_jobs / 向量任务表

```sql
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
```

记录每次 rebuild 的状态。默认 provider 使用 `mock-embedding-v0`；当 `LUCAS_EMBEDDING_PROVIDER=bge-m3` 时，这里会写 `BAAI/bge-m3`。失败时写入 `error`。

### embedding_vectors / 向量引用表

```sql
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
  deleted_at TEXT
);
```

默认 `vector_store = mock`；设置 `LUCAS_VECTOR_STORE=sqlite-vec` 后，真实向量写入 sqlite-vec vec0 虚拟表，表名按维度区分，例如 `lucas_vec_embeddings_1024`。`embedding_vectors` 仍是 Lucas 侧引用表，metadata 会保存 `sqlite_vec_table`、`sqlite_vec_rowid` 和 8 维 preview。sqlite-vec 返回命中后，也必须通过 `chunk_id` 回到 Lucas Database 读取权威内容。
