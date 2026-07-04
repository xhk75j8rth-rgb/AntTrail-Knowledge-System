# AntTrail Database / 蚁迹数据库

AntTrail Database 是一个本地个人结构化知识数据库。它使用 Node / 节点模型：每个节点既可以保存内容视图，也可以继续拥有无限层级子节点。它同时提供桌面 UI 和本地 HTTP API，方便用户和 Agent 读写同一份 SQLite 数据。

结构化知识主数据使用 `ComposedCardV1`。Node 内容、Markdown View、卡片信息和 Graph View 都从 Canonical Card JSON 派生，SiYuan 或其他 Markdown 工具只作为展示型 Sink，不作为主 Schema。

## 安装

```bash
npm install
```

V0 使用 Node.js 内置 SQLite，需要 Node.js `22.5.0` 或更高版本。

## 开发启动

同时启动本地 API 和 Web UI：

```bash
npm run dev
```

启动 Electron 桌面壳：

```bash
npm run dev:electron
```

也可以只启动 API：

```bash
npm run start:api
```

Web UI 默认地址是 `http://127.0.0.1:5173`。本地 API 默认地址是 `http://localhost:8765`。

左侧边栏底部有 `设置` 按钮。点击后可以查看本地 API 地址、Token 状态，并生成或重置本地 API Key。

## 本地 API

健康检查：

```bash
curl http://localhost:8765/api/health
```

默认端口是 `8765`，可通过环境变量修改：

```bash
LUCAS_DB_PORT=8766 npm run start:api
```

## SQLite 数据库

默认数据库文件在：

```text
data/lucas.db
```

可以通过 `LUCAS_DB_PATH` 指定完整数据库路径，或通过 `LUCAS_DB_DATA_DIR` 指定数据目录。

附件文件默认保存在：

```text
data/attachments
```

可以通过 `LUCAS_DB_ATTACHMENTS_DIR` 指定独立附件目录。

## API Token

所有写入 API 都需要：

```http
Authorization: Bearer <token>
```

推荐设置：

```bash
LUCAS_DB_API_TOKEN=your-local-token npm run dev
```

如果未设置，V0 使用本地开发默认 Token：

```text
lucas-local-dev-token
```

前端默认也会使用这个开发 Token。若设置了自定义 `LUCAS_DB_API_TOKEN`，通过浏览器访问 Web UI 时也应设置：

```bash
VITE_LUCAS_DB_API_TOKEN=your-local-token npm run dev
```

Electron 启动时会把 `LUCAS_DB_API_TOKEN` 传给渲染进程。

如果没有设置环境变量，V0 也支持在设置面板里生成本地 API Token。生成后可直接复制给 Agent 使用。

Agent 访问示例：

```bash
curl -X GET "http://localhost:8765/api/tree" \
  -H "Authorization: Bearer <YOUR_API_KEY>"
```

```bash
curl -X POST "http://localhost:8765/api/write-by-path" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <YOUR_API_KEY>" \
  -d '{
    "path": "/项目A/开发日志/Agent测试",
    "content": "Agent 写入测试",
    "mode": "append",
    "actor": "agent"
  }'
```

## 主要功能

- 左侧 NodeTree / 节点树：展示、展开、折叠、选择、创建子节点、软删除、刷新。
- 节点行右侧 `+`：悬停或选中时显示，点击后直接在当前节点下创建子节点。
- 右侧 ContentEditor / 内容编辑器：编辑标题和内容视图，900ms debounce 自动保存，也可手动保存。
- 节点附件：支持上传图片、视频和任意本地文件；图片直接预览，视频内嵌播放，其他文件显示文件名和大小。
- 左下角 `设置`：查看本地 API 地址、Token 状态，生成或重置本地 API Key。
- 结构化卡片入库：`POST /api/cards/ingest` 接收 `ComposedCardV1`、原始材料、质量门控和渲染视图。
- 卡片信息面板：当前节点可查看挂载卡片标题、一句话总结和标签。
- Graph View V0：默认以 Obsidian 风格 2D 深色 canvas 展示图谱，支持局部/全库、1-3 层深度、拖拽、缩放、适配视图、悬停提示和实体类型过滤；也可切换到 Three.js 3D 探索模式。
- SQLite Storage / SQLite 存储：真实落库，关闭后重新启动仍保留内容。
- Agent API / Agent 可调用 API：节点 CRUD、追加内容、按路径写入、搜索。

## 文档

- [docs/API.md](docs/API.md) / API 文档
- [docs/DATA_MODEL.md](docs/DATA_MODEL.md) / 数据模型说明
- [docs/CARD_STORAGE_MODEL.md](docs/CARD_STORAGE_MODEL.md) / 卡片存储模型设计
- [docs/CARD_INGEST_API.md](docs/CARD_INGEST_API.md) / 卡片入库 API 文档
- [docs/KNOWLEDGE_GRAPH_V0_PLAN.md](docs/KNOWLEDGE_GRAPH_V0_PLAN.md) / 知识图库 V0 实施计划
- [docs/CARD_TO_GRAPH_MAPPING.md](docs/CARD_TO_GRAPH_MAPPING.md) / 卡片到图谱映射规则
- [docs/GRAPH_DATA_MODEL.md](docs/GRAPH_DATA_MODEL.md) / 图谱数据模型
- [docs/GRAPH_API.md](docs/GRAPH_API.md) / 图谱 API 设计
- [docs/VECTOR_INDEXING_PLAN.md](docs/VECTOR_INDEXING_PLAN.md) / 向量索引计划
- [docs/CHUNKING_STRATEGY.md](docs/CHUNKING_STRATEGY.md) / 切块策略
- [docs/EMBEDDING_PROVIDER.md](docs/EMBEDDING_PROVIDER.md) / 向量模型接入说明
- [docs/VECTOR_STORE_ADAPTER.md](docs/VECTOR_STORE_ADAPTER.md) / 向量库存储适配说明
- [docs/SIYUAN_MIGRATION.md](docs/SIYUAN_MIGRATION.md) / 从 SiYuan 迁移到 Lucas Database
- [docs/HANDOFF.md](docs/HANDOFF.md) / Agent 交接记录
