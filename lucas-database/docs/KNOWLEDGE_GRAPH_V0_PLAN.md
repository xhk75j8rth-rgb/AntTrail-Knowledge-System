# KNOWLEDGE_GRAPH_V0_PLAN.md / 知识图库 V0 实施计划

## 目标

Knowledge Graph V0 在 Lucas Database 中建立结构化知识图库的最小底座：

- 基于 `ComposedCardV1`、`cards`、`nodes`、`node_relations` 构建图谱。
- 由规则映射生成稳定关系，优先覆盖卡片、节点、概念、标签、风险、行动、方法、来源和模型。
- 提供 API 查询、手动新增关系、软删除关系、按卡片重建规则关系。
- 在工作区右侧提供 Obsidian 风格的深色 canvas 图谱入口，展示节点、卡片、虚拟实体和结构关系。

## 已落地范围

- SQLite 已有 `cards`、`card_*`、`card_rendered_views`、`node_relations`、`card_events` 表。
- `node_relations` 增加 `deleted_at`，删除关系采用软删除。
- `GET /api/graph?node_id=<node_id>&depth=1&scope=local` 返回局部图谱数据。
- `GET /api/graph?scope=global&depth=1` 返回最近 Node/Card 组成的全库图谱视图。
- `POST /api/relations` 新增手动关系。
- `DELETE /api/relations/:id` 软删除关系。
- `POST /api/cards/:id/rebuild-graph` 根据卡片结构重建规则型关系。
- `POST /api/cards/ingest` 入库后会触发规则型图谱重建。
- 前端 `GraphView.tsx` / `GraphPanel.tsx` 可切换局部/全库、选择 1-3 层深度，并以 canvas 力导向图展示点线网络。
- 前端新增 2D / 3D 显示模式切换：2D 是默认工作视图，3D 使用 Three.js 作为探索和展示视图。
- 图谱画布支持拖拽节点、拖动画布、滚轮缩放、适配视图、悬停提示和按实体类型图例过滤。

## 非目标

- 不做向量数据库。
- 不做 AI 自动关系抽取主流程。
- 不读取网页，不做 OCR。
- 不从 SiYuan Markdown 反解析图谱。
- 不改 `ComposedCardV1` 主 schema。
- 不破坏 Node 树、内容编辑器、API Key 设置和现有节点 API。

## V0 工作流

1. 接入 Agent 提交 `ComposedCardV1` 到 `POST /api/cards/ingest`。
2. 服务端写入目标 Node、Card、知识块、条目、来源材料、质量门和渲染视图。
3. 服务端根据卡片字段生成 `node_relations`。
4. 前端或 Agent 调用 `GET /api/graph` 获取局部或全库图谱。
5. 卡片更新后调用 `POST /api/cards/:id/rebuild-graph` 重建规则型关系。

## 下一步

- 增加卡片更新 API，并在更新后自动重建图谱。
- 为关系删除增加更完整的 relation event 记录。
- 增加按实体类型、关系类型过滤图谱的查询参数。
- 增加图谱搜索、聚焦节点和更大数据量下的布局性能优化。
- 3D 模式已通过 `React.lazy` / dynamic import 拆分为按需加载 chunk。
