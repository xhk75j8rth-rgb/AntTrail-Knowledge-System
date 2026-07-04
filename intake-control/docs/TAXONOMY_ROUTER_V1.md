# Taxonomy Router V1

## 目标

`TaxonomyRouter / 分类路由模块` 位于知识卡写入前，用于把 `ComposedCardV1` 路由到稳定的知识库分类路径。

它不是模型自由命名目录，也不是 storage sink。V1 只输出结构化分类决策，后续由 SiYuan writer 或 Lucas Database sink 决定如何使用该结果。

```text
ComposedCardV1
-> TaxonomyRouter
-> TaxonomyDecisionV1
-> future Storage Layer
```

## 输入

V1 接收 `ComposedCardV1` 形态的 `dict`，优先读取：

- `display_title`
- `one_sentence_summary`
- `core_points`
- `knowledge_blocks`
- `tags`
- `reusable_value`
- `source_title`

## 输出

核心输出是 `TaxonomyDecisionV1`：

```json
{
  "schema_name": "TaxonomyDecisionV1",
  "card_id": null,
  "recommended_path": ["AI", "Agent", "记忆系统"],
  "path_id": "ai/agent/memory",
  "confidence": "high",
  "matched_existing_category": true,
  "should_create_category": false,
  "similar_categories": [],
  "reason": "内容与已有分类 AI / Agent / 记忆系统 匹配；命中分类关键词：AgentOS、记忆。",
  "fallback": null,
  "create_proposal": null
}
```

低置信度时默认推荐 `Inbox / 待分类`。如果没有相似分类、但材料有足够主题信号，会附带 `CategoryCreateProposalV1`。当 `taxonomy_auto_create_from_proposal=true` 且 proposal 通过安全策略时，`recommended_path` 会直接使用 proposal 路径，供后续 sink 写入相关分类。

## 初始分类树

V1 内置轻量 seed，与当前纸面推演树对齐：

- `AI / Agent / 记忆系统`
- `AI / Agent / 上下文管理`
- `AI / Agent / 工具调用`
- `AI / Agent / 多 Agent 协作`
- `AI / Agent / AgentOS 架构`
- `AI / 知识库 / 结构化卡片`
- `AI / 知识库 / 知识图谱`
- `AI / 知识库 / 向量检索`
- `AI / 知识库 / 数据库适配`
- `AI / 内容生产 / AI 写作`
- `AI / 内容生产 / 短视频工作流`
- `AI / 内容生产 / 图像 / 视频生成`
- `AI / 模型与 Provider / DeepSeek`
- `AI / 模型与 Provider / OpenAI`
- `AI / 模型与 Provider / Claude`
- `AI / 模型与 Provider / 多模型路由`
- `AI / 工程化 / 任务队列`
- `AI / 工程化 / 质量门禁`
- `AI / 工程化 / 存储层`
- `AI / 工程化 / 自动化流水线`
- `软件工程 / 前端开发 / 动画与交互`
- `软件工程 / 前端开发 / AI 辅助前端开发`
- `软件工程 / 前端开发 / UI 工程`
- `软件工程 / 前端开发 / 工具链`
- `项目 / Lucas-Knowledge-DB-Lab`
- `项目 / Lucas-AgentOS`
- `项目 / Lucas-SiYuan-Codex`
- `项目 / AIGC 视频工作台`
- `Inbox / 待分类`

## 匹配策略

V1 使用规则 + 简单相似度：

1. 从知识卡字段收集主题文本。
2. 对已有分类做关键词命中、token overlap、轻量 fuzzy ratio。
3. 达到阈值时推荐既有分类。
4. 多个候选时选择最高分，并保留 `similar_categories`。
5. 未达到阈值时先构造分类 proposal。
6. 若 `taxonomy_auto_create_from_proposal=true` 且 proposal 不是受保护主题，使用 proposal 路径作为写入目标；否则进入 `Inbox / 待分类` 并保留人工审核 proposal。
7. AI 根路径不是默认兜底；候选路径以 `AI` 开头时，必须先有 AI / Agent / 模型 / 知识系统 / 工程化等域内证据，才允许成为推荐或相似分类。

## 歧义处理

- `AI` 既可以是一等知识领域，也可以只是工具属性。若材料主语是前端、动画、UI、构建工具等具体工程领域，即使包含 AI/Codex/Claude Code/Cursor，也优先归入对应领域；AI 相关路径只作为相似分类或标签。
- 真实知识树快照里的 AI 路径也需要经过域内证据准入。若快照中残留 `AI / 内容生产 / 穿搭`，普通穿搭、服装搭配、防晒、妆容、护肤、旅行、美食等生活方式主题不能继续写入该 AI 路径；启用 `taxonomy_auto_create_from_proposal=true` 时应写入 `生活方式 / <主题>`，未启用时才保守进入 `Inbox / 待分类` 并保留 proposal。
- `AI / 内容生产 / <主题>` 不能只因为材料出现“内容”“视频”等泛词而自动生成；必须有明确 AI 内容生产证据，例如 AI 写作、AI 视频、AIGC、文生图、文生视频、图像/视频生成工具或生成式内容工作流。
- `GSAP`、`ScrollTrigger`、前端动效、页面交互动画优先归入 `软件工程 / 前端开发 / 动画与交互`；只有主题明确是“如何用 AI 生成前端页面/组件/工作流”时，才归入 `软件工程 / 前端开发 / AI 辅助前端开发`。
- `AI Coding` 如果讨论研发流程、质量门禁、测试、稳定交付、平台治理，仍归入 `AI / 工程化 / 质量门禁` 或 `AI / 工程化 / 自动化流水线`，不要因为出现 React/前端词就转入前端分类。
- `长期记忆`、`用户记忆`、`跨任务记忆` 优先归入 `AI / Agent / 记忆系统`；只在主题明确是 prompt window 或会话压缩时归入 `上下文管理`。
- `Claude Code`、`OpenAI`、`DeepSeek` 等 provider 命中不能压过任务类型；例如 Claude Code 写小说归入 `AI / 内容生产 / AI 写作`。
- `SiYuan`、`Storage Layer`、`database sink` 如果讨论知识库主数据迁移，优先归入 `AI / 知识库 / 数据库适配`；如果讨论写入实现、配置或 sink 工程，可归入 `AI / 工程化 / 存储层`。
- `MCP`、`Python Service`、部署边界类内容优先判断是否是生产主链路治理；若是，归入 `AI / 工程化 / 自动化流水线`，并把 `AI / Agent / 工具调用` 作为相似分类。
- `OCRMaterialV1`、`source material`、画面文字证据当前归入 `AI / 知识库 / 结构化卡片`；后续如果材料量增加，可拆出 `AI / 知识库 / 多模态证据`。
- 电商客服、行业客服、业务流程自动化等当前没有稳定分类时，在默认配置下进入 `Inbox / 待分类` 并输出 `AI / 行业应用 / ...` proposal；如果启用 `taxonomy_auto_create_from_proposal=true`，可直接写入该 proposal 路径。
- 职场、职业发展、生活方式、法律、项目等明确非受保护主题在没有既有分类时，如果启用 `taxonomy_auto_create_from_proposal=true`，应写入对应 proposal 路径，不应仅因分类树缺项堆到 `Inbox / 待分类`。
- 短碎片、无实体、无上下文、无可复用主题时进入 `Inbox / 待分类`，且不生成新分类 proposal。

## 纸面样例固化

当前单测已覆盖纸面推演的 8 个样例：

- AgentOS 长期记忆 -> `AI / Agent / 记忆系统`
- MirrorFish + Claude Code 写小说 -> `AI / 内容生产 / AI 写作`
- 向量数据库 vs SQL -> `AI / 知识库 / 向量检索`
- SiYuan 替换为数据库 API -> `AI / 知识库 / 数据库适配`
- MCP 与 Python Service 部署边界 -> `AI / 工程化 / 自动化流水线`
- 抖音 OCR source material -> `AI / 知识库 / 结构化卡片`
- 电商客服自动化 -> 默认 `Inbox / 待分类` + `CategoryCreateProposalV1`；启用自动 proposal 后可写入 `AI / 行业应用 / 客服自动化`
- 职场真诚沟通 -> 启用自动 proposal 后写入 `职场`
- 普通通勤穿搭 -> 启用自动 proposal 后写入 `生活方式 / 穿搭`，不进入 `AI / 内容生产 / 穿搭`
- 平价防晒小物 -> 启用自动 proposal 后写入 `生活方式 / 防晒`
- AI 火柴人 PPT 科普视频 -> 启用自动 proposal 后写入 `AI / 内容生产 / PPT演示`
- AI 冲击程序员职业壁垒 -> 启用自动 proposal 后写入 `职业发展 / 程序员`
- 无主题碎片 -> `Inbox / 待分类`，不 proposal

## 真实知识树快照

V1 现在支持从 `runtime/taxonomy_tree.json` 或 `config.taxonomy_tree_path` 指定的 JSON 文件读取真实知识树快照。快照格式仍是 `TaxonomyTreeV1`；如果路径里包含根节点 `知识卡`，router 会自动去掉该根节点，只把后续路径作为分类路径。

可用 SiYuan HTTP API 导出当前树：

```powershell
python tools\export_siyuan_taxonomy_tree.py --config config\link_pipeline.json --output runtime\taxonomy_tree.json
```

主流程可通过 `taxonomy_refresh_from_siyuan=true` 在正式卡通过质量门禁后、分类前自动刷新快照。刷新失败不会阻断入库；router 会继续使用已有快照或 seed。

相关配置：

- `taxonomy_tree_path`：默认 `runtime/taxonomy_tree.json`。
- `taxonomy_tree_merge_seed`：是否把 seed 分类和真实快照合并，默认 `false`，避免 seed 抢过真实树。
- `taxonomy_auto_create_from_proposal`：无既有/相似分类但有明确 proposal 时，是否把 proposal 作为目标路径，默认 `false`。
- `siyuan_use_taxonomy_path`：SiYuan writer 是否消费 `taxonomy_decision.json`，默认 `true`。
- `taxonomy_export_root_path`：导出知识树时读取的 SiYuan 路径，当前本机用 `/`，因为“知识卡”是 notebook/UI 根而不是文档路径。
- `siyuan_taxonomy_root_path`：SiYuan 写入分类路径的前缀；空字符串表示直接写到当前 notebook 根目录下。

## 当前边界

- 不调用真实模型。
- 不调用 OCR。
- 不抓取评论。
- `TaxonomyRouter` 本身不写 SiYuan；SiYuan writer 会在正式卡已有 `taxonomy_decision.json` 时使用分类路径。
- 不写 Lucas Database。
- 不修改 `ComposedCardV1`。
- `TaxonomyRouter` 本身不调用 storage；主流程只在正式卡通过 quality gate 后落盘 `taxonomy_decision.json`，供后续 sink 消费。

## 后续接入建议

后续可在 `card_composer + quality_gate` 通过后、storage sink 写入前调用：

```python
from taxonomy_layer import TaxonomyRouter

decision = TaxonomyRouter().route_card(composed_card)
```

再把 `decision` 写入 job 记录，并交给 SiYuan / Lucas Database sink 决定目标目录或数据库分类字段。

当前 `tools/run_link_job.py` 已提供这一职责边界：正式 `ComposedCardV1` 通过 `quality_gate` 后，会按配置刷新知识树快照、写入 job 目录下的 `taxonomy_decision.json`。SiYuan writer 会优先写入 `<siyuan_taxonomy_root_path>/<recommended_path>/<title>`；Lucas Database / Brain sink 会优先使用该文件生成 `/知识卡/<recommended_path>/<title>`；若文件缺失，才回退到 Inbox、tag 或 source_type 兜底路径。
