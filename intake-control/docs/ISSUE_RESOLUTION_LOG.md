# Issue Resolution Log

本文件记录已经定位并修复的问题，尤其是入库链路中的门禁、模型、读取、写入和任务状态问题。

记录目标：

- 把“问题 -> 原因 -> 修复 -> 验证 -> 后续规则”沉淀在项目固定位置。
- 避免把完整复盘散落在聊天、临时 handoff 或不相关文档里。
- 给后续 agent 一个可检索的排障索引。

记录规则：

- 只记录已经完成定位并有明确修复或稳定结论的问题。
- 不粘贴密钥、cookie、账号、完整日志或大段用户材料。
- Handoff 只保留很短的交接指针；详细复盘写在本文件。
- 如果问题属于某个专题文档的稳定规则，也要同步更新对应文档，例如 `docs/INTAKE_SYSTEM_RULES.md`。

## 2026-07-04 Chat Gateway 短续问“软件工程呢”误答没有直接相关内容

### 问题

用户在 `/ui` 对话里问“软件工程呢”，助手回答“没有找到与软件工程直接相关的内容”，并把可用知识说成主要集中在 AI 写作/小说创作和前端动画开发。该回答不符合实际知识库状态，因为 AntTrail Database 中存在 `/知识卡/软件工程` 节点。

### 原因

UI 会把最近对话历史放到 `metadata.conversation_history`。Chat Gateway 对短句续问原先会把当前问题和最近历史拼成检索 query，导致 `软件工程呢` 这类 `X呢` 主题短句被上一轮话题污染。底层 Database 对 `软件工程` 本身可命中 `/知识卡/软件工程`，但聊天层混合上下文后，模型根据弱相关来源生成了“没有直接相关”的不准确话术。

另一个数据边界是：`/知识卡/软件工程` 节点目前只有标题 chunk，详细内容主要来自附近的 AI 编程、前端开发、工程化和程序员职业冲击卡片。正确表达应是“有该分类/节点，但主体内容较少”，不是“没有直接相关”。

### 修复

- `ai_layer/chat_responder.py` 新增短主题续问规范化：例如 `软件工程呢` 会先变成 `软件工程` 再作为 RAG query。
- 规范化只处理真正的尾部语气词 `呢/吗/嘛/吧/呀/啊`，不会压缩普通长问题或误删“什么”。
- RAG prompt 增加约束：如果来源标题或路径与当前问题核心词直接匹配，必须承认这是直接相关来源，不能回答“没有直接相关内容”。
- 新增回归测试 `test_chat_responder_normalizes_short_topic_followup_for_retrieval`。

### 验证

```powershell
python -B -m unittest tests.test_ai_layer -v
python -B -m unittest tests.test_chat_gateway -v
```

结果：AI 层 40 tests OK；Chat Gateway 56 tests OK。重启 live `3963` 后，带历史 payload 复测 `软件工程呢` 返回 `retrieval.query=软件工程`，Source 1 为 `软件工程`，路径 `/知识卡/软件工程`。

### 后续规则

- 短主题续问 `X呢` / `X吗` / `X吧` 应优先按 `X` 检索，不要把上一轮助手长回复拼进 query。
- 当检索命中的是标题/分类节点但正文很少时，回复应区分“有这个节点/分类”和“缺少详细内容”。

## 2026-07-04 Chat Gateway 数据库 RAG 显示失败

### 问题

Chat Gateway 普通问答里，数据库 RAG 显示为失败或不可用；直接调用 Lucas Database `POST /api/agent/retrieve` 返回 `500 INTERNAL_ERROR`。用户怀疑仍在指向旧项目。

### 原因

数据库 API 端口和路由本身正常，`127.0.0.1:8765` 指向 MergeTest `lucas-database\data\lucas.db`。真正失败点是 BGE-M3 embedding worker：当前 MergeTest 项目缺少 `lucas-database\.venv-bge-m3\Scripts\python.exe`，而旧 `<old-database-project>\.venv-bge-m3` 仍存在且可用，说明项目迁移时漏带本地向量模型运行环境。

另一个误导项是 `intake-control\.env` 仍保留旧 `LUCAS_DB_BASE_URL=http://127.0.0.1:8768`，虽然 Chat Gateway RAG 实际通过 `storage_config.py` 读取 `config/storage.local.json` 的 `8765`，但该旧值会误导排障。

### 修复

- 将旧 `.venv-bge-m3` 复制到当前项目 `lucas-database\.venv-bge-m3`。
- 修改 `lucas-database/backend/indexing/bgeM3EmbeddingProvider.ts`：默认 venv、相对 `LUCAS_BGE_M3_PYTHON` 和相对 worker 路径都按当前 `lucas-database` 项目根解析，不再跟随启动 cwd 或旧项目目录。
- 将 `intake-control\.env` 的 `LUCAS_DB_BASE_URL` 改为 `http://127.0.0.1:8765`。
- 重启 live `127.0.0.1:8765` API，保留 `LUCAS_EMBEDDING_PROVIDER=bge-m3` 和 `LUCAS_VECTOR_STORE=sqlite-vec`。

### 验证

已执行：

```powershell
npm run typecheck
.\.venv-bge-m3\Scripts\python.exe -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
Invoke-RestMethod http://127.0.0.1:8765/api/health
Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/agent/retrieve ...
Invoke-RestMethod -Method Post http://127.0.0.1:3963/api/chat/messages ...
```

结果：当前项目 venv 可用，CUDA 为 `True`；`/api/health` 显示 `bge-m3` + `sqlite-vec`；直接 `POST /api/agent/retrieve` 返回 `ok=true`、`status=ready`、`answerability.can_answer=true`；Chat Gateway 返回 `data.retrieval.ok=true`、`can_answer=true`、`context_text_present=true`，目标 Base URL 为 `http://127.0.0.1:8765`。

### 后续规则

- MergeTest 运行层不得再依赖 `<old-database-project>`；旧目录只作为迁移源和历史对照。
- RAG 报 `INTERNAL_ERROR` 时，要先区分端口/认证问题和 embedding worker 问题；`BGE-M3 python executable not found` 属于本地模型运行环境缺失，不是数据库 API 未连接。
- 文档、UI 占位符和启动示例应优先使用当前 live 端口 `8765`。

## 2026-07-04 微信桥误显示为终端助手底座

### 问题

Lucas 在 `/ui` 点击“微信桥”后，弹窗状态显示旧终端底座，启动日志也显示旧的终端助手桥接进程。用户期望的是微信连接 Lucas Chat Gateway：链接自动入库，普通消息进入数据库问答；界面和日志都不应出现终端助手底座字样。

### 原因

旧实现用 `CLI-WeChat-Bridge` 的终端助手模式作为底层收发通道，再在 vendor hook 中优先拦截 URL。这样链接虽然能进入 Lucas intake，但进程、日志和 UI 仍暴露底层 adapter，并且非链接消息会落到终端助手路径，不符合“微信 -> Lucas Chat Gateway”的产品语义。

### 修复

- 新增 `wechat-bridge/lucas-wechat-gateway-bridge.mjs`：直接复用开源包的 WeChat 登录、长轮询和回发能力，所有微信文本都转发到 `POST /api/chat/messages/async`。
- 新增 `wechat-bridge/scripts/start-lucas-wechat-gateway-bridge.ps1`：启动 Lucas Gateway 桥，不再传 `-Adapter`，不启动终端助手或 shell runtime。
- `server/chat_api.py` 的 `/api/wechat-bridge/start/status/stop` 改为新脚本，状态为 `mode=chat_gateway`，并声明 `handles_links=true`、`handles_plain_text=true`。
- `/ui` 弹窗改为显示“能力：链接入库 + 数据库问答”，启动请求不再发送 adapter 字段。
- 清理旧桥进程后启动新桥；新桥复用本机已保存微信登录态时不会显示二维码，只有登录缺失或过期时才输出扫码内容。
- 后续补充：状态接口新增 `saved_login_present`，UI 新增“重新扫码登录”；强制扫码会先停当前桥进程树，再用 `-ForceRelogin` 启动并输出二维码。

### 验证

已执行：

```powershell
python -B -m py_compile server\chat_api.py tests\test_chat_gateway.py
node --check ..\wechat-bridge\lucas-wechat-gateway-bridge.mjs
python -B -m unittest tests.test_chat_gateway -v
```

结果：`tests.test_chat_gateway` 56 tests OK。live `127.0.0.1:3963` 已重启，新 `/api/wechat-bridge/status` 返回 `mode=chat_gateway`、`handles_plain_text=true`、`handles_links=true`；live `/ui` 源码检查确认不含旧底座字样并包含“重新扫码登录”。当前微信桥 PID 为 `24812`，状态 running，`saved_login_present=true`；`/api/wechat-bridge/stop` 会停止 Windows 进程树，避免留下 Node 子进程。未主动发送真实链接，未触发真实模型、OCR、评论、SiYuan 或 Brain 写入；后续真实处理由 Lucas 从微信发消息触发。

### 后续规则

- 用户可见微信桥路径不得暴露终端助手底座，也不得把非链接消息交给终端助手或 shell。
- 微信桥应作为 Chat Gateway adapter：链接走异步入库，普通文本走现有 fallback / RAG / 数据库问答。
- 若已保存微信登录态，启动时不出现二维码是正常行为；只有登录缺失或过期时才需要扫码。

## 2026-07-03 待分类继续增长：清晰非 AI 主题仍被保守拦截

### 问题

Lucas 截图指出 `Inbox / 待分类` 继续增长，包含 `反季节夏日穿搭灵感`、`火柴人PPT科普视频...`、`几秒钟就破了我十年功力，程序员该如何破局`、`超平价防晒小物推荐` 等已经具备明确主题的正式卡。

### 原因

上一轮修复阻止了普通穿搭误入 `AI / 内容生产 / 穿搭`，但策略过于保守：

- `生活方式 / 穿搭` 这类 lifestyle proposal 被 `allows_auto_create_from_proposal()` 显式禁止，即使 `taxonomy_auto_create_from_proposal=true` 也只能落到 Inbox。
- `should_propose_category()` 在存在弱 `similar_categories` 时直接放弃 proposal，导致 AI/PPT 科普视频和程序员职业冲击内容虽然主题明确，却被几个低分 AI sibling 匹配卡住。
- 当前真实 taxonomy 快照里已有 `职场`，但单段精确命中的分数可能只有 `0.455`，低于 `0.48` 推荐阈值，容易把已有分类也当成低置信相似项。

### 修复

- `taxonomy_layer/category_policy.py` 允许明确非受保护根类在自动 proposal 开启时落地，包括 `生活方式`、`职业发展`、`职场`、`法律`、`项目`。
- 生活方式仍不会进入 AI：穿搭、防晒、护肤、美妆、旅行、美食等会生成 `生活方式 / <主题>`；AI 根路径仍必须有明确 AI 域内证据。
- AI 内容生产和职业发展等清晰主题会在弱相似存在时继续生成 proposal，避免低分 sibling 阻断分类。
- 非 AI 现有分类的精确命中若接近阈值，也可作为 medium 推荐，避免 `职场` 这类已有节点因 0.455 分落回 Inbox。
- 新增回归测试覆盖穿搭、防晒、AI/PPT 科普视频、程序员职业冲击，以及职场 proposal 的临时树隔离。

### 验证

未运行真实链接、真实模型、OCR、评论抓取、SiYuan 写入或 Brain 写入，未调用 MCP。

```powershell
python -B -m py_compile taxonomy_layer\category_policy.py taxonomy_layer\taxonomy_router.py tests\test_taxonomy_router.py
python -B -m unittest tests.test_taxonomy_router -v
python -B -m unittest tests.test_lucas_database_sink tests.test_siyuan_writer -v
python -B -m py_compile tools\run_link_job.py tools\write_siyuan.py tools\write_lucas_database.py
```

结果：

- `tests.test_taxonomy_router` 共 29 tests OK。
- `tests.test_lucas_database_sink` + `tests.test_siyuan_writer` 共 14 tests OK。
- 使用当前真实 `runtime/taxonomy_tree.json` 对截图对应历史 job 只读重算：
  - `20260703-211213-049f0bf9` -> `生活方式 / 穿搭`
  - `20260703-212837-42bbb3f9` -> `生活方式 / 防晒`
  - `20260703-212515-80d854d6` -> `AI / 内容生产 / PPT演示`
  - `20260703-212540-95562180` -> `职业发展 / 程序员`
- 已重启 live `127.0.0.1:3963`，当前 PID `42804`；只读 `GET /api/system/status` 返回 `ok=true`、`project_root` 指向 MergeTest `intake-control`、`config_warnings=[]`。

### 后续规则

- “不塞进 AI”不等于“全部待分类”。正式卡材料充足且主题明确时，应优先使用安全 proposal 路径。
- 弱相似项只能作为参考，不能阻断明确新分类 proposal。
- 本轮未迁移已经写入 `Inbox / 待分类` 的旧笔记；如果要清理历史待分类，需要走单独的受控迁移/修订流程。

## 2026-07-03 Chat Gateway 普通问答没有稳定先查 Lucas Database

### 问题

Lucas 指出 ChatAway / Chat Gateway 已有短暂记忆，但普通知识问答还没有把 Lucas Database 向量库作为 RAG 先查来源。目标是：用户问相关问题时，先从数据库找答案，再让模型基于证据回答。

接入完成后的首轮 live 验证又暴露一个残余问题：Lucas Database 直接检索可用，但 Chat Gateway 侧 `data.retrieval.status="connection_error"`、`error="timed out"`，导致最终回复仍是模型泛答。

### 原因

- 项目规则明确 MCP 只能 debug-only，生产路径不能依赖 MCP 授权或 MCP server；因此应接 Lucas Database 已有 HTTP `POST /api/agent/retrieve`，而不是把 MCP 写进 Chat Gateway。
- Chat Gateway 缺少一个稳定的 Agent Retrieve API client、prompt 注入和结构化返回字段。
- 初版 RAG 客户端虽然已接入，但 `_retrieve_for_chat()` 默认把 `LUCAS_CHAT_RAG_TIMEOUT_SEC` 的默认值限制到最多 8 秒；本地真实 BGE-M3/sqlite-vec 在冷启动或偶发慢查询时可能超过 8 秒，导致 Gateway 超时后只把“检索不可用”放进 prompt。

### 修复

- 新增 `ai_layer/lucas_retrieval_client.py`，通过 Lucas Database HTTP API 调用 `/api/agent/retrieve`，读取 `context.text`、`sources`、`citations`、`answerability`、`confidence` 和 `warnings`。
- `ai_layer/chat_responder.py` 对普通知识型问题先尝试检索；问候、状态追问、配置问答和修订请求跳过检索。
- 检索可回答时，把 Lucas Database context 注入模型 prompt，并要求用 `[Source n]` 引用；低置信或不可用时，明确告诉模型不能声称已从数据库查到答案。
- `chat_gateway/handlers/fallback_handler.py` 在响应中暴露 `data.retrieval`，前端和调试请求可以看到是否真的检索成功。
- RAG 默认超时从最多 8 秒放宽到 45 秒；`LUCAS_CHAT_RAG_TIMEOUT_SEC` 可配置，最大 120 秒。底层 `LucasDatabaseRetriever.retrieve()` 默认超时同步调为 45 秒。
- 已重启 live `127.0.0.1:3963`，启动时设置 `LUCAS_CHAT_RAG_TIMEOUT_SEC=45`。

### 验证

未调用 MCP，未运行真实链接入库、OCR、评论抓取、SiYuan 写入或 Brain 写入。

```powershell
python -B -m py_compile ai_layer\lucas_retrieval_client.py ai_layer\chat_responder.py ai_layer\prompt_registry.py chat_gateway\handlers\fallback_handler.py tests\test_ai_layer.py
python -B -m unittest tests.test_ai_layer -v
python -B -m unittest tests.test_chat_gateway -v
```

结果：

- `tests.test_ai_layer` 共 39 tests OK。
- `tests.test_chat_gateway` 共 51 tests OK。
- `GET http://127.0.0.1:8765/api/health` 返回 MergeTest `lucas-database\data\lucas.db`，embedding provider 为 `bge-m3`，vector store 为 `sqlite-vec`。
- 直接 `POST http://127.0.0.1:8765/api/agent/retrieve` 查询 `App Store Connect 上架 iPhone 应用需要准备什么` 返回 `status=ready`、`answerability.can_answer=true`、`source_count=3`。
- live `POST http://127.0.0.1:3963/api/chat/messages` 同样问题返回 `data.retrieval.attempted=true`、`ok=true`、`status=ready`、`can_answer=true`、`context_text_present=true`，首个来源为 `AI 生成 iPhone App 后如何准备上架 App Store`，模型回复包含 `[Source 1]` 引用。

### 后续规则

- Chat Gateway RAG 生产路径只接 Lucas Database HTTP API，不接 MCP。
- 排查“没有先查库”时先看响应里的 `data.retrieval`，不要只看自然语言回复。
- 如果 `retrieval.status=connection_error` 且 error 为 timeout，先检查 `LUCAS_CHAT_RAG_TIMEOUT_SEC`、Lucas Database health、BGE-M3 worker 状态和 `/api/agent/retrieve` 直接调用结果。
- 冷启动真实 BGE-M3 可能慢；不要把 8 秒以内的超时误判为“向量库不可用”。

## 2026-07-03 平台授权测试失败与授权窗口自动关闭

### 问题

Lucas 反馈：小红书已经在授权浏览器里登录，但 `/ui` “测试授权”仍显示不通过；抖音和今日头条点击授权/重新授权后授权界面打不开。同时提出新交互要求：授权成功后自动关闭授权窗口；如果一直没有完成授权，90 秒后自动关闭。

### 原因

旧授权探测混用了两类端口：

- 固定授权窗口端口：抖音 `11441`、今日头条 `11442`、小红书 `11443`；
- 历史 headless 探测或抽取进程留下的随机 CDP 端口。

当平台 profile 被随机端口的 headless / 旧窗口占用时，UI 可能看到 profile 存在却无法连接固定授权窗口；测试按钮还可能尝试用同一个持久 profile 再起 headless 探针，进一步让“已登录但测试失败”和“重新授权不开窗”的状态混在一起。

### 修复

- `active_debug_port()` 只认平台固定授权端口，不再把 session 文件里的随机端口当成授权窗口。
- `authorization_status()` 对外展示固定端口，避免旧随机端口污染 UI 判断。
- `test_authorization()` 在发现已有平台 profile 浏览器进程占用但固定 CDP 不可用时，不再启动随机 headless 探针，而是明确返回 `cdp_unavailable` 并引导重新授权。
- 如果没有平台 profile 进程占用，测试按钮可用固定端口临时启动一次 profile 探测，并在结束后清理该平台 profile 浏览器进程。
- `open_authorization()` 打开可见授权窗口后启动 90 秒自动监控：只读当前授权窗口的登录/验证码/平台标记；检测到授权成功即关闭该平台授权窗口，90 秒未授权也关闭。
- `/ui` 点击授权后会轮询平台状态，看到授权成功或 `authorization_timeout` 后停止轮询并显示对应提示。

### 验证

未调用 MCP，未运行真实链接入库、模型、OCR、评论抓取、SiYuan 或 Brain 写入。

```powershell
python -B -m py_compile intake_platforms.py server\chat_api.py tests\test_chat_gateway.py
node -e "<parse ui/minimal_chat/index.html inline scripts>"
python -B -m unittest tests.test_chat_gateway.ChatGatewayTests.test_reauthorize_force_reopen_closes_profile_browser_before_launch tests.test_chat_gateway.ChatGatewayTests.test_authorization_status_ignores_stale_random_debug_port tests.test_chat_gateway.ChatGatewayTests.test_authorization_test_does_not_spawn_probe_when_profile_process_occupies_without_fixed_cdp tests.test_chat_gateway.ChatGatewayTests.test_xiaohongshu_authorization_test_endpoint_returns_probe_result tests.test_chat_gateway.ChatGatewayTests.test_ui_route_returns_minimal_chat_page -v
python -B -m unittest tests.test_chat_gateway -v
```

结果：

- `py_compile` 通过。
- UI 内联脚本解析结果为 `1 script OK`。
- 授权相关 5 个窄测 OK。
- `tests.test_chat_gateway` 共 53 tests OK。

### 后续规则

- 平台授权窗口只能以固定 CDP 端口作为可连接依据；随机端口只能视为临时/历史进程信号，不能标为授权窗口。
- 重新授权前应关闭命令行中包含该平台 Lucas browser profile 的 Edge / Chrome 进程，避免浏览器复用旧 profile 进程吞掉新窗口。
- 授权窗口打开后应自动收口：授权成功关闭；90 秒未授权关闭；不把授权窗口长期留给后续读取任务占用。

## 2026-07-03 OCR 图片进入 Brain 后仍显示断图

### 问题

Lucas 截图反馈 Brain / Lucas Database 卡片中 OCR 图片显示为断图，图片 alt 如 `OCR frame 10`、`OCR frame 2`，且有些卡片仍显示“未提取到画面文字证据”。

### 原因

最新真实 job `20260703-194415-286aadf1` 证明 OCR 本身已运行成功：`ocr_status=ocr_done`、`text_items=163`、抽样帧 10 张，最终筛选出 `GitHub AI密钥泄漏扫描器` 和 `Codex` 两张关键帧。问题不在本次 OCR 提取，而在入库展示资产：

- `tools/write_lucas_database.py` 会把本机 `LucasVideoOCR/.../frame_010.jpg` 改写成 intake 临时图片路由，例如 `http://127.0.0.1:3963/api/jobs/<job_id>/ocr-images/frame_010.jpg`；
- Lucas Database 旧逻辑看到 payload 已有 `asset_url` 后，只把该 URL 写进 Markdown，不再复制本机 `frame_path` 到自己的附件资产；
- 结果是 Brain 卡片长期依赖 intake 服务和 TEMP 帧图。一旦 3963 服务、job allowlist 或 TEMP 文件不可用，前端就断图。

另有 job `20260703-193837-0211b0a1` 为 `frame_extract_failed`，确实没有抽出 OCR 图片；这类情况应继续报告 OCR 边界，不能伪装成已提取。

### 修复

修复点在 Lucas Database：

- `POST /api/cards/ingest` 现在对 `image_worth_saving=true` 的 OCR evidence，即使已有 intake 临时 HTTP `asset_url`，也会在本机 `frame_path` 可读时复制图片到 Lucas Database 附件存储，生成 `/api/assets/att_xxx`。
- Markdown 会同时替换本机 `frame_path` 和 intake 临时 HTTP 图片 URL。
- 幂等命中既有卡片时也会执行 OCR 资产化，刷新 `card_rendered_views` 和最新 `card_source_materials.metadata_json.ocr_evidence_items`，但不重复创建 Node/Card 或重建图谱。
- 已重启 `127.0.0.1:8765` API，并对真实 job `20260703-194415-286aadf1` 只重写 `main` 数据库目标。

### 验证

未重新跑真实链接、真实 OCR、模型、评论抓取或 SiYuan 写入；只对既有 job 执行 Lucas Database main 目标幂等刷新。

```powershell
npm run typecheck
npm run build
python tools\write_lucas_database.py --job-dir runtime\jobs\20260703-194415-286aadf1 --target-id main --request-filename lucas_database_main_ingest_request_asset_fix.json --result-filename write_lucas_database_main_result_asset_fix.json
```

结果：

- 临时库 smoke：首次 ingest 生成 1 个 `/api/assets/...` 附件，第二次同幂等键不重复复制。
- 真实卡 `card_95129b2e-b442-4a32-99a8-25b3e8b95992` 回执 `created:false, updated:true`。
- `GET /api/cards/card_95129b2e-b442-4a32-99a8-25b3e8b95992` 中 Markdown 不再包含 `127.0.0.1:3963/api/jobs` 或 `LucasVideoOCR`。
- 两个新资产 URL 均返回 `200 image/jpeg`。

### 后续规则

- 排查 OCR 图片断图时，先区分 OCR 是否真的提取失败：看 `ocr.json.status`、`ocr_material.json.sampling.frames`、`evidence_items` 和 `lucas_database_*_ingest_request.json.source_material.ocr_evidence_items`。
- 如果 OCR 已提取但 Brain 断图，优先检查 Database 是否已把图片资产化成 `/api/assets/...`，不要长期依赖 intake 的 `/api/jobs/<job_id>/ocr-images/...`。
- 如果旧 job 的 TEMP `frame_path` 已被清理，无法从临时 URL 自动恢复图片，需要重新跑 OCR 或手工补附件。

## 2026-07-03 小红书重新授权按钮不开新窗口

### 问题

Lucas 在 `/ui` 的“链接路由与授权”面板看到抖音、今日头条、小红书均提示“浏览器会话不可连接”，并且点击“小红书 / 重新授权”后没有出现新的授权界面。

### 原因

“浏览器会话不可连接”表示本机平台 profile 或历史状态存在，但对应 CDP 调试端口不可用，不能证明登录态可复用。旧实现点击“重新授权”时只是再次用同一个 `--user-data-dir` 启动 Edge / Chrome；如果旧授权浏览器进程已经占用同一个 profile，浏览器可能直接复用旧进程、吞掉新窗口请求，导致用户看不到新的授权界面。

### 修复

- `intake_platforms.open_authorization()` 新增 `force_reopen` 参数。
- `/api/intake/authorize/{platform_id}` 会接收 UI 传来的 `force_reopen=true`。
- UI 的“重新授权”按钮在存在本机会话、测试失败或 `cdp_unavailable` 时自动带上 `force_reopen=true`。
- `force_reopen` 只关闭命令行中明确包含该平台 Lucas browser profile 路径的 Edge / Chrome 进程，不关闭普通浏览器窗口。
- 浏览器启动参数新增 `--new-window`，并在成功打开后提示“登录后点击测试授权”。

### 验证

未调用 MCP，未运行真实链接入库、模型、OCR、评论抓取、SiYuan 或 Brain 写入。

```powershell
python -B -m py_compile intake_platforms.py server\chat_api.py tests\test_chat_gateway.py
python -B -m unittest tests.test_chat_gateway -v
node -e "<parse ui/minimal_chat/index.html inline scripts>"
```

结果：

- `tests.test_chat_gateway` 共 51 tests OK。
- UI 内联脚本解析结果为 `2 script OK`。
- 重启 3963 后，`/ui` 返回包含 `data-force-reopen`；`POST /api/intake/authorize/xiaohongshu` 携带 `force_reopen=true` 返回 `browser_opened`。
- 本机真实授权窗口验证关闭了 11 个旧小红书 profile 浏览器进程；随后 `http://127.0.0.1:11443/json/list` 返回 200，说明新授权窗口 CDP 端口已可连接。

### 后续规则

- 授权状态的 `ready_unverified` / “会话待测试” 不是登录成功。
- `cdp_unavailable` 时应使用带 `force_reopen=true` 的重新授权流程，而不是要求用户手动猜测旧窗口是否占用 profile。

## 2026-07-03 普通穿搭内容被自动塞进 AI 分类

### 问题

Lucas 发现入库前分类会把穿搭等非 AI 主体内容放到 `AI / 内容生产 / 穿搭`。这不是单条模型判断偏差，而是分类规则会把普通生活方式内容错误写进 AI 大类。

### 原因

本机 `runtime/taxonomy_tree.json` 已经存在 `AI / 内容生产 / 穿搭` 这个真实树路径。分类器读取真实树后，如果继续完全信任已有路径，就会形成错误自强化。

同时 `taxonomy_auto_create_from_proposal=true` 时，`CategoryPolicy.build_create_proposal()` 会因为材料里出现泛词“内容”或“视频”，把任意有用标签提案成 `AI / 内容生产 / <标签>`。普通穿搭卡即使没有 AI、Agent、模型、AIGC 或知识系统证据，也会被自动提案到 `AI / 内容生产 / 穿搭`。

### 修复

- `TaxonomyRouter` 在采用相似度结果前会调用 `CategoryPolicy.allows_category_match()`，AI 根路径必须有明确 AI / Agent / 模型 / 知识系统 / 工程化等域内证据才允许成为候选。
- `CategoryPolicy.build_create_proposal()` 不再因为泛词“内容”“视频”就生成 `AI / 内容生产 / <标签>`；AI 内容生产提案必须先满足 AI 内容生产证据。
- 普通穿搭、服装搭配、妆容、护肤、旅行、美食等生活方式主题不会进入 `AI / 内容生产`；本条目当时采用的保守 Inbox 策略已在后续“待分类继续增长”修订中放宽，启用 `taxonomy_auto_create_from_proposal=true` 时可写入 `生活方式 / <主题>`。
- 明确 AI 场景仍可走 AI 分类。例如“AI 穿搭视频生成工作流”可在自动提案开启时使用 `AI / 内容生产 / 穿搭`。
- 修复了 `tests.test_taxonomy_router` 中一个会受本机 `runtime/taxonomy_tree.json` 污染的 helper 测试，改用临时知识树。

### 验证

未运行真实链接、真实模型、OCR、评论抓取、SiYuan 写入或 Brain 写入，未调用 MCP。

```powershell
python -B -m py_compile taxonomy_layer\category_policy.py taxonomy_layer\taxonomy_router.py tests\test_taxonomy_router.py
python -B -m unittest tests.test_taxonomy_router -v
python -B -m unittest tests.test_lucas_database_sink tests.test_siyuan_writer -v
python -B -m py_compile tools\run_link_job.py tools\write_siyuan.py tools\write_lucas_database.py
```

结果：

- `tests.test_taxonomy_router` 共 24 tests OK。
- `tests.test_lucas_database_sink` + `tests.test_siyuan_writer` 共 13 tests OK。
- 使用当时真实 `runtime/taxonomy_tree.json` 复测普通穿搭样例，结果为 `recommended_path=["Inbox","待分类"]`，`create_proposal.proposed_path=["生活方式","穿搭"]`，`similar_categories=[]`；该保守结果已在后续修订中改为 `生活方式 / 穿搭`。
- 已重启 `http://127.0.0.1:3963` 的 MergeTest uvicorn，当前 PID `92620`；只读 `GET /api/system/status` 返回 `ok=true`、`project_root` 指向 MergeTest `intake-control`、`config_warnings=[]`。

### 后续规则

- 真实知识树快照里已经存在的 AI 路径不等于一定可信；AI 根路径必须经过域内证据准入。
- 生活方式、消费、穿搭等非 AI 主体内容不要自动塞进 `AI / 内容生产`；后续规则已放宽为：自动 proposal 开启且主题明确时写入 `生活方式 / <主题>`，否则才进入 `Inbox / 待分类`。
- 既有 SiYuan 里的错误文件夹或旧笔记没有在本轮移动；若要整理历史路径，需要走单独的受控修订/迁移流程。

## 2026-07-03 OCR / 页面分析后浏览器后台持续播放

### 问题

Lucas 反馈分析链接触发 OCR 时，视频网页会在后台一直开启并播放；要求 OCR 提取时不能播放视频，并且任务完成、卡片写入后自动关闭这些分析网页。

### 原因

CDP 页面读取链路里存在两个风险点：

- `fetch_douyin_page_playwright.py` 和小红书 reader 的浏览器启动参数使用了 `--autoplay-policy=no-user-gesture-required`，相当于放宽自动播放限制；
- 抖音页面采集脚本为了更容易发现视频源，曾主动执行 `video.play()`；
- `ocr_web_images.py` / 小红书 reader 在复用授权浏览器 CDP 端口时，会直接使用已有 page target 导航到任务链接，结束时只断开 CDP，不会关闭该浏览器标签页。

自行启动的 headless 浏览器通常会在脚本结束时 kill，但复用授权浏览器时留下的任务页签可能继续播放。

### 修复

- `tools/fetch_douyin_page_playwright.py`、`tools/fetch_xiaohongshu_note.py`、`tools/ocr_web_images.py`、`tools/fetch_comments.py`、`tools/fetch_webpage.py` 统一改为 `--autoplay-policy=user-gesture-required`。
- 这些 CDP 脚本在导航前注入 `mediaPlaybackGuardScript()`：静音并暂停所有 `video/audio`，移除 `autoplay`，覆盖 `HTMLMediaElement.play()`，并监听新插入媒体节点。
- 移除了抖音页面采集中的主动 `video.play()`，改为只读取 DOM source、performance resource 和网络请求里已经暴露的媒体 URL。
- `ocr_web_images.py` 和 `fetch_xiaohongshu_note.py` 复用授权浏览器时会通过 `/json/new` 创建临时 target / 标签页，采集结束后先暂停媒体，再通过 `/json/close/<target_id>` 或 `Page.close` 关闭临时 target；不关闭用户授权主窗口。
- `ocr_web_images.py` 顶层结果新增 `opened_temporary_target` 与 `page_cleanup`，便于复盘是否暂停/关闭过页面。
- `docs/INTAKE_SYSTEM_RULES.md` 和 `.agents/skills/link-intake-classifier/SKILL.md` 已沉淀 CDP 页面生命周期规则。

### 验证

未运行真实模型、未执行正式链接入库、未写入 SiYuan 或 Brain，未调用 MCP。

```powershell
python -B -m py_compile tools\ocr_web_images.py tools\fetch_xiaohongshu_note.py tools\fetch_douyin_page_playwright.py tools\fetch_comments.py tools\fetch_webpage.py tests\test_ocr_material.py
python -B -m unittest tests.test_ocr_material.OCRMaterialTests.test_cdp_page_scripts_block_media_playback tests.test_ocr_material.OCRMaterialTests.test_reused_browser_extractors_use_temporary_targets_and_close_them -v
python -B -m unittest tests.test_ocr_material tests.test_chat_gateway -v
rg -n "autoplay-policy=no-user-gesture-required|video\.play\(|audio\.play\(|\.play\(\)" -S tools scripts
```

结果：

- `tests.test_ocr_material` + `tests.test_chat_gateway` 共 77 tests OK。
- Node 语法解析 5 个内嵌 CDP 脚本通过。
- 静态扫描生产脚本未发现 `video.play()` / `audio.play()` / `autoplay-policy=no-user-gesture-required` 残留。
- 非写入烟测 `python -B tools\ocr_web_images.py --url "https://example.com/" ...` 返回 `web_image_ocr_no_images`，并记录 `page_cleanup.paused_media=true`。
- 使用 Lucas 提供的抖音链接做非写入 `ocr_web_images.py` 烟测时，页面图片捕获成功 `capture_status=images_captured`，但 RapidOCR 初始化失败 `bad allocation`，因此 OCR 文字识别未完成；该失败与页面关闭/播放控制无关。

### 后续规则

- 后续不能为了抓取媒体源重新加入 `video.play()`；如果确需媒体 URL，应优先从 DOM、performance resource、Network 事件和下载器输出中读取。
- 复用平台授权浏览器时，只关闭任务临时 target，不要关闭用户授权主窗口。
- RapidOCR `bad allocation` 属于独立资源问题；排障时看 `ocr.json.capture_status` 与 `status=ocr_failed`，不要误判为页面捕获失败。

## 2026-07-03 Lucas Database runtime cutover 到 MergeTest 目录

### 问题

Lucas 指出数据库目录就在 `<repo-root>\lucas-database`，但实际 live 运行还停在旧 `<old-database-project>` 副本上，`GET /api/health` 也把 `database.path` 报成旧目录，说明运行层没有真正合并。

### 原因

之前虽然合并了代码和 UI 改动，但数据库服务启动时仍沿用了旧副本里的进程和 data 目录。数据迁移和 live cutover 被混在一起处理，导致“文件已经在项目里，但 runtime 还在旧位置”。

### 修复

- 将旧 `<old-database-project>\data` 复制到 `<repo-root>\lucas-database\data`。
- 停止旧 8765 / 5173 进程。
- 从 MergeTest `lucas-database` 目录重新启动 API 和 Vite。

### 验证

- `GET http://127.0.0.1:8765/api/health` 返回 `database.path=<repo-root>\lucas-database\data\lucas.db`。
- `GET http://127.0.0.1:5173/` 返回 MergeTest 前端。
- 监听进程 command line 都指向 `<repo-root>\lucas-database`。

### 后续规则

- 多项目副本并存时，不能只看当前 shell cwd；必须同时核对监听端口、进程 command line 和 health 里的真实 data path。
- 数据迁移完成不等于 runtime cutover 完成，两个步骤都要验证。

## 2026-07-03 Live 端口仍跑旧 DB-Lab，MergeTest 新项目缺本机配置

### 问题

Lucas 要求最终从 `<repo-root>\intake-control` 启动，但现场同时存在多个 uvicorn：`3963` 仍服务 DB-Lab 指纹，`3964/3965` 服务 MergeTest 指纹。MergeTest 服务起初 `GET /api/system/status` 为 degraded，原因是新项目目录没有本机 `.env`，Brain key 检测缺失。

### 原因

代码和运行配置分散在两份项目副本：旧 DB-Lab 拥有 live 端口和本机 `.env` / storage 配置，新 MergeTest 拥有最新合并代码但缺少被 `.gitignore` 保护的本机配置文件。原状态接口也没有暴露 `project_root`、`config_path`、`runtime_dir`，导致只能靠 UI 指纹和端口间接判断 live 服务来自哪份项目。

### 修复

- 为 `server/chat_api.py` 的 `/api/system/status` 增加 `runtime.project_root`、`runtime.config_path`、`runtime.runtime_dir`、`runtime.ui_path`，后续可直接确认 live 项目根。
- 将 DB-Lab 的本机 `.env`、`config/storage.local.json`、`config/link_pipeline.json` 迁到 MergeTest `intake-control`。这些文件均受 `.gitignore` 保护；复盘只记录 key 名称存在，不记录任何 key/token 值。
- 停止旧 DB-Lab `3963` uvicorn 和临时 `3964/3965` staging uvicorn，从 `<repo-root>\intake-control` 启动新的 `3963` live 服务。

### 验证

- `python -B -m py_compile server\chat_api.py storage_config.py tools\run_link_job.py tools\write_lucas_database.py`：通过。
- `python -B -m unittest tests.test_ai_layer tests.test_storage_submit_api tests.test_chat_gateway tests.test_lucas_database_sink -v`：104 tests OK。
- `GET http://127.0.0.1:3963/api/system/status`：`ok=true`，`project_root` 指向 MergeTest `intake-control`，`config_warnings=[]`，AI/SiYuan/Brain 均 configured。
- `POST /api/chat/messages` dry-run：`runner_called=false`，未启动真实任务。
- `POST /api/storage/test`：SiYuan `connected`，Brain `connected_schema_rejected`，两者 `write_attempted=false`。
- UI 静态检查：内联脚本解析 OK，旧标记 `dry-run/dryRun/张明/测试链接` 无残留。

### 后续规则

- live 切换或排障时先查 `/api/system/status.runtime.project_root`，不要只看 shell 当前目录。
- 本机配置缺失时可以迁移 `.env` 和 ignored config，但不得打印或记录密钥值。
- 不要为了查询旧 job 把 live 切回 DB-Lab；旧历史 job 如需保留，应单独迁移结构化 artifacts。

## 2026-07-03 MergeTest 与 DB-Lab 存储配置改动未对齐

### 问题

Lucas 发现当前工作目录和旧项目副本之间出现版本错位：旧 DB-Lab 副本里已经存在 Agent 配置、进入数据库按钮、多 Brain 数据库目标等 UI/存储配置改动，但真实目标应是 `<repo-root>\intake-control`。同时，存储页“测试连接/测试链接”容易再次退化成打开页面，而不是做 API 连通性探测。

### 原因

机器上同时存在 `Lucas-Knowledge-DB-Lab` 和 `Lucas-Knowledge-System-MergeTest\intake-control` 两份相近项目。之前部分 agent 改在 DB-Lab，MergeTest 只包含其中一部分修复：已有 `/api/storage/test`、`storage_targets` 和 async progress，但缺少 DB-Lab 后续的多 Brain 目标配置、writer 按目标逐个调用、Agent 本地配置和 UI 顶部数据库入口。

不能整文件覆盖：DB-Lab 仍有与 MergeTest 当前规则冲突的旧测试期望，例如单链接长回执和 dry-run 队列 completed 语义。因此本轮只迁移与存储配置和用户已确认丢失 UI 改动相关的小块。

### 修复

- `storage_config.py` 支持 `database_targets`、`selected_database_target_ids`、`storage_write_enabled/enable_storage_write`，并暴露 `resolve_lucas_database_runtimes()`。
- `tools/run_link_job.py` 使用 `resolve_lucas_database_runtimes()`，当 Brain 目标启用时按已选数据库目标逐个调用 writer，并记录 `lucas_database_write_results`。
- `tools/write_lucas_database.py` 新增 `--target-id`、`--result-filename`、`--request-filename`，每个 Brain 目标有独立 request/result artifact。
- `/ui` 对齐 DB-Lab 已有 UI 改动：Agent 配置、本地 `AGENT_CONFIG_KEY`、顶部进入数据库按钮、多数据库目标列表、移除硬编码“张明”和页面 dry-run 开关。
- `/ui` 保留并明确存储 API 探测语义：全局和目标卡“测试连接”仍调用 `POST /api/storage/test`；数据库目标行只提供“打开页面”，不再叫“测试链接”。
- 新增/补齐测试：多数据库目标保存不泄露 key、关闭所有写入仍保留目标、总开关关闭时 `storage_targets` 不回退到 SiYuan、UI 不含 dry-run/张明/测试链接旧标记。

### 验证

未运行真实链接、真实模型、OCR、评论抓取、SiYuan 写入或 Brain 写入。未调用 MCP。

```powershell
python -B -m py_compile server\chat_api.py storage_config.py chat_gateway\config_preflight.py ai_layer\chat_responder.py ai_layer\prompt_registry.py chat_gateway\handlers\fallback_handler.py chat_gateway\handlers\link_handler.py chat_gateway\link_extractor.py chat_gateway\message_router.py tools\run_link_job.py tools\write_lucas_database.py tools\card_composer.py tests\test_ai_layer.py tests\test_chat_gateway.py tests\test_storage_submit_api.py tests\test_lucas_database_sink.py
python -B -m unittest tests.test_ai_layer tests.test_storage_submit_api tests.test_chat_gateway tests.test_lucas_database_sink -v
node -e "const fs=require('fs'); const html=fs.readFileSync('ui/minimal_chat/index.html','utf8'); const scripts=[...html.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)]; for (const [idx,m] of scripts.entries()) { new Function(m[1]); console.log('script', idx+1, 'ok'); } if(/dry-run|dryRun|张明|测试链接/.test(html)) { process.exit(1); }"
```

结果：

- Python 编译通过。
- 104 tests OK。
- UI 内联脚本解析通过，旧标记检查通过。

### 后续规则

- 多项目副本对齐时，先确认目标项目根目录；旧副本只能作为参考，不要整文件覆盖。
- 存储配置对齐必须同时检查 UI、`storage_config.py`、`run_link_job.py`、`write_lucas_database.py` 和测试。
- “测试连接”只能做 API 探测；需要打开数据库 Web UI 时，按钮文案必须是“进入数据库”或“打开页面”。
- Brain 多目标时，排障要看 `result.json.lucas_database_write_results` 和每个 `write_lucas_database_<target>_result.json`，不要只看主 `write_lucas_database_result.json`。

## 2026-07-03 小红书视觉内容未触发图片兜底导致仍未通过

### 问题

Lucas 再次发送小红书链接后仍未正式入库，并指出“只有视频/图片的内容起码要抓取一点视频内容或放几张图片”。真实运行副本 DB-Lab 中最新两个 job 为：

- `20260703-171858-05caa597`
- `20260703-171600-f1499be9`

两者都写入了 SiYuan 临时来源材料卡，但没有成为正式卡，也没有被 Brain 主库接收为正式知识卡。

### 原因

两个 job 的 `content.status=cdp_failed`，错误为 `CDP endpoint unavailable: /json/list; fetch failed`，提示旧授权浏览器可能占用了持久 profile 且没有 remote debugging 端口。用户随链接文本长度只有 61 / 65，材料门禁结果为 `primary_material_too_short` 和 `reader_failed_without_transcript`。

旧逻辑只在 `image_count > 0` 且 `needs_visual_enrichment=true` 时触发 `ocr_web_images.py`。小红书 reader 失败时 `image_count=0`、`needs_visual_enrichment=false`，所以没有进入网页图片捕获/OCR，也没有在来源材料卡中展示图片证据。这不是单纯“质量门禁提高”，而是视觉平台 reader 失败后的图片兜底触发条件过窄。

Brain 主库返回 `422 QUALITY_GATE_FAILED` 是因为卡片仍是非正式 `extracted_source_card`、`quality_gate.passed=false`；另一个 `database_2` 返回 `404 page not found` 是该目标配置到了 SiYuan 地址 `127.0.0.1:6806`，属于单独的存储目标配置问题。

### 修复

- `tools/run_link_job.py` 新增并接入视觉平台兜底：小红书、抖音、`mixed/*`、`video/*` 在材料不足且 reader 失败、平台正文为空或需要视觉增强时，即使 `image_count` 为 0 / unknown，也会尝试现有 `ocr_web_images.py`。
- 来源材料卡新增“图片证据”段，读取 `ocr_material.json` 中的采样帧或证据图片路径，最多展示 6 张图片。
- 新增测试覆盖小红书 `cdp_failed + image_count=0` 仍触发网页图片 OCR，以及图片证据 markdown 渲染。
- 项目 skill 已补充同类排障规则：视觉平台 reader 失败时先尝试图片兜底，不能只报“质量不够”。
- `docs/INTAKE_SYSTEM_RULES.md` 已同步 OCR 边界：视觉平台 Source Reader 失败或平台正文为空时，不能只因 `image_count=0` 放弃网页图片捕获/OCR。

### 验证

未运行新的真实链接、真实 OCR、真实模型、SiYuan 写入或 Brain 写入。

```powershell
python -B -m py_compile tools\run_link_job.py tests\test_chat_gateway.py
python -B -m unittest tests.test_chat_gateway.ChatGatewayTests.test_visual_platform_reader_failure_triggers_web_image_ocr_without_image_count tests.test_chat_gateway.ChatGatewayTests.test_format_ocr_image_evidence_uses_captured_frame_paths -v
python -B -m unittest tests.test_chat_gateway -v
$env:PYTHONUTF8='1'; python <codex-home>\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\link-intake-classifier
```

结果：

- Python 编译通过。
- 新增视觉兜底窄测 2 tests OK。
- `tests.test_chat_gateway` 46 tests OK。
- skill validate 通过。

### 后续规则

- 对视觉平台链接，`cdp_failed` / `source_reader_failed` 后不能因为 `image_count=0` 就停止；应先尝试网页图片捕获/OCR，并把图片证据写入来源/临时卡。
- 如果图片兜底仍无图、登录墙或反爬阻断，继续降级为来源材料卡，并明确说明边界。
- 非正式卡不得伪装成正式知识卡；正式入 Brain 仍需模型写卡与质量门禁通过。
- `database_2` 指向 SiYuan Base URL 导致 `404 page not found`，应作为存储配置问题单独修正或禁用。

## 2026-07-03 刚重跑仍未正式入库：旧服务进程与域名标题误判

### 问题

Lucas 反馈修完“链接外正文入库”后刚刚又不行。实际运行副本最新真实 job 显示：

- `20260703-163746-5703e18e`：低置信临时卡；
- `20260703-163810-fa534cf6`：材料足够、模型写卡成功，但质量门禁失败并降级为临时复核卡。

### 原因

1. 3963 端口 uvicorn 仍是旧内存代码，HTTP dry-run 响应没有 `data.source_text_present`；最新两个 `run_link_job.py` 命令行也只有 `--url`，没有 `--source-text-file`。
2. `20260703-163810-fa534cf6` 的材料已足够：`primary_sources=['source_text','ocr']`，composer 成功；但 `card_quality_gate.py` 把相邻旧 job 的 `comments.json.title=www.douyin.com` 当成跨 job 标题，触发 `cross_job_title:www.douyin.com`，属于域名标题误判。
3. Brain 返回 `422 QUALITY_GATE_FAILED` 是正确保护：该 job 当时 `quality_gate_passed=false`，不能当正式卡接收。SiYuan 已写入临时复核卡。

### 修复

- 已重启实际运行副本 3963 Chat Gateway，重启后 dry-run 返回 `source_text_present=true` 和非零 `source_text_length`。
- `card_quality_gate.py` 新增 sibling title 候选过滤：裸域名、URL、平台名等低信息标题不参与 `cross_job_title` 判断。
- 新增测试 `test_quality_gate_ignores_domain_only_sibling_titles`。
- 项目 skill 已补充：刚改代码后需验证 live dry-run 是否有 `data.source_text_present`；`cross_job_title:www.douyin.com` 属于域名标题误判。

### 验证

未运行新的真实链接、未调用真实模型、未写入 SiYuan 或 Brain。

```powershell
Invoke-RestMethod http://127.0.0.1:3963/health
POST /api/chat/messages dry-run 文本+链接
python -B -m py_compile tools\card_quality_gate.py tests\test_ai_layer.py
python -B -m unittest tests.test_ai_layer.AILayerTests.test_quality_gate_ignores_domain_only_sibling_titles tests.test_ai_layer.AILayerTests.test_quality_gate_passes_with_formal_summary_type -v
```

结果：

- 3963 `/health` 正常；
- dry-run 返回 `source_text_present=true`、`source_text_length=37`；
- gate 相关测试通过；
- 用修复后的 gate 复核 `20260703-163810-fa534cf6` 的既有 composed card，`quality_gate_passed=true`；
- 未触发真实写入。

### 后续规则

- 修改入口代码后，真实重跑前必须用 live HTTP dry-run 验证服务是否加载新代码。
- `cross_job_title` 失败要看具体标题；裸域名/平台名不能作为串 job 证据。
- 已经写成临时卡的 job 不自动重写 Brain；若要补写，需要单独进入真实写入确认。

## 2026-07-03 普通聊天回复过长且重复卡片化

### 问题

Lucas 反馈 `/ui` 里普通追问和确认类消息会被 agent 扩写成很长的状态报告，并且经常附带“建议下一步”或类似卡片段落；链接入库成功后也会把同一句摘要在开头和“入库卡片”里重复展示，阅读负担很重。

### 原因

普通 fallback 消息直接交给聊天模型，prompt 只要求“简洁”，但没有明确禁止普通追问输出卡片式分节，也没有对“现在好了吗”这类无 job_id / 无工具结果的状态追问做确定性短答。单链接回执层则把 `reply_card.one_sentence_summary` 同时渲染在“摘要”和“入库卡片”的“一句话摘要”字段中。

### 修复

- `ChatResponder` 新增短状态追问保护：没有 job/tool 结果时，像“现在好了吗”只返回边界短答，不调用模型，不编造网络恢复、正在查询或入库状态。
- 存储配置问答默认压缩为 3 行以内；只有用户明确说“展开/详细/详情”等才列每个目标的 Base URL、Endpoint 和 key 状态。
- `chat_responder_system_prompt()` 明确普通追问、确认、问候和澄清只回 1 到 3 句；除非有真实 intake/job 结果或用户明确要求，不输出卡片式分节。
- 单链接成功回执改为“摘要 / 值得学习 / 接入结果 / 文件树”，不再输出“入库卡片”块，也不重复“一句话摘要”。
- `.agents/skills/link-intake-classifier/SKILL.md` 新增聊天短答与卡片边界规则，后续 agent 遇到同类问题先按此规则处理。

### 验证

未运行真实链接处理、真实模型调用、OCR、评论抓取、SiYuan 写入或 Lucas Database 写入。

```powershell
python -B -m py_compile ai_layer\chat_responder.py ai_layer\prompt_registry.py chat_gateway\handlers\link_handler.py tests\test_ai_layer.py tests\test_chat_gateway.py
python -B -m unittest tests.test_ai_layer tests.test_chat_gateway -v
POST http://127.0.0.1:3964/api/chat/messages  # 普通短状态追问与存储配置问答
```

结果：

- Python 编译通过。
- `tests.test_ai_layer` + `tests.test_chat_gateway` 共 79 tests OK。
- 重启 3964 预览服务后，`现在好了吗` 与 `我有哪些知识库接入` 均返回 `model_called=false`；短状态追问未编造网络/查询/入库状态，配置问答默认 3 行。

### 后续规则

- 普通聊天和状态追问不要默认带卡片、清单或“建议下一步”；必要动作或排障时才展开。
- 配置类问题先读本地配置，默认给 compact answer；用户要求详情时再展开到每个目标。
- 真实链接入库结果可以展示接入结果，但同一句摘要不得在同一气泡里重复出现。

## 2026-07-03 链接外用户正文被入口丢弃导致低质量降级

### 问题

Lucas 指出：如果用户发的是一大串文本加链接，平台页面、转写或 OCR 失败时，系统不应该只因为页面材料不足就判定不能真实入库；这段随链接文本本身有摘要价值。若视频没有可用 OCR 文件且像静态图片，也应尝试下载/捕获图片材料后写入。

### 原因

Chat Gateway 同步 handler 和 UI 后台队列只把 `--url` 传给 `tools/run_link_job.py`，`MessageEvent.text` 中 URL 之外的正文在入口层被丢弃。`run_link_job.py` 的材料门禁已经支持足够长的 `source_text` 进入正式写卡，但它看不到用户随链接提供的文本。Douyin 主链路在视频 OCR 没有输入时也没有调用已有 `ocr_web_images.py` 网页图片兜底。

### 修复

- `chat_gateway/link_extractor.py` 新增 `extract_context_text()`，剥离 URL 后保留用户正文。
- `chat_gateway/handlers/link_handler.py` 将用户正文写入 TEMP 下临时 JSON，通过 `--source-text-file` 传给 runner；同步、单链接和多链接并行路径都会传递。
- `server/chat_api.py` 的异步 batch 路径同样保留 `source_text_present/source_text_length` 并向后台 runner 传 `--source-text-file`。
- `tools/run_link_job.py` 新增 `--source-text` / `--source-text-file`，写入 `input.json` 与 `content.json` 的 `user_supplied_text`，材料门禁会给出 `user_supplied_text_sufficient`。
- `tools/card_composer.py` 将 `user_supplied_text` 纳入 `source_material.primary_text`，但仍要求模型 composer 和 quality gate 通过才能成为正式卡。
- Douyin 主链路在转写/OCR 后若材料仍不足且没有可用视频 OCR 输入，会尝试已有 `ocr_web_images.py` 网页图片捕获/OCR 兜底。
- 项目 skill `.agents/skills/link-intake-classifier/SKILL.md` 已补充：长文本随链接必须作为合法当前 job 材料排查；低质量但用户选择写入时先用用户文本和图片 OCR 兜底，不能伪装正式卡。

### 验证

未运行真实链接处理、真实模型调用、OCR、评论抓取、SiYuan 写入或 Lucas Database 写入。

```powershell
python -B -m py_compile chat_gateway\link_extractor.py chat_gateway\handlers\link_handler.py server\chat_api.py tools\run_link_job.py tools\card_composer.py tests\test_chat_gateway.py
python -B -m unittest tests.test_chat_gateway.ChatGatewayTests.test_link_context_text_keeps_non_url_material tests.test_chat_gateway.ChatGatewayTests.test_single_link_runner_receives_user_supplied_source_text tests.test_chat_gateway.ChatGatewayTests.test_link_runner_passes_source_text_file_to_child_process tests.test_chat_gateway.ChatGatewayTests.test_http_api_async_link_returns_realtime_batch_without_running_real_task tests.test_chat_gateway.ChatGatewayTests.test_material_quality_gate_allows_user_supplied_text_as_primary_material tests.test_chat_gateway.ChatGatewayTests.test_douyin_low_material_can_trigger_web_image_ocr_fallback -v
python -B -m unittest tests.test_chat_gateway -v
python -B -m unittest tests.test_lucas_database_sink tests.test_storage_submit_api -v
$env:PYTHONUTF8='1'; python <codex-home>\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\link-intake-classifier
```

结果：

- Python 编译通过。
- 新增/相关窄测 6 tests OK。
- `tests.test_chat_gateway` 43 tests OK。
- `tests.test_lucas_database_sink` + `tests.test_storage_submit_api` 19 tests OK。
- 项目 skill 校验通过。

### 后续规则

- 排查“质量不够/没有真实入库”时，先查 `input.json.user_supplied_text_present`、`content.json.user_supplied_text_length` 和 `material_quality.primary_sources`。
- `user_supplied_text` 足够时是合法来源材料，不应被 `primary_material_too_short` 覆盖。
- 没有可用视频 OCR 输入时，可以尝试网页图片捕获/OCR，但若页面无图、登录墙或反爬阻断，仍只能生成临时/来源材料卡。

## 2026-07-03 存储配置测试按钮误跳转且目标勾选未落到主链路

### 问题

Lucas 反馈 `/ui` 存储配置里的“测试连接/测试链接”按钮应该测试 API 是否连通，而不是直接跳转页面；同时需要验证 SiYuan 和 Brain API 来回切换，以及只勾选 SiYuan、只勾选 Brain、两个都勾选时，真实入库目标是否和 UI 勾选一致。

### 原因

旧 UI 只有单个 active storage provider 配置和手动 Brain 测试卡入口，缺少“只测试连接、不写卡”的 storage test API。主链路写入目标实际由 `config/link_pipeline.json` 的 `storage_targets` 控制，而 UI 保存存储 provider 时没有把勾选目标同步到该配置；SiYuan writer 还读取 `siyuan_base_url`，如果只保存 `storage.local.json` 会出现 UI 显示地址和真实写入地址不一致。

### 修复

- `storage_config.py` 增加 `storage_targets` / `auto_ingest_enabled` 读写，保存目标时同步 `config/link_pipeline.json`，保存 SiYuan provider 时同步 `siyuan_base_url`。
- `server/chat_api.py` 新增 `POST /api/storage/test`，用于非写入连接测试。
- SiYuan 测试调用 `/api/notebook/lsNotebooks`，验证服务和 token，不创建笔记。
- Brain 测试向 `/api/cards/ingest` 发送故意不合格的 probe payload；`400/422` 视为服务和 token 已通过到 schema 层，`401/403` 视为 auth 失败，不生成知识卡。
- `/ui` 存储页增加自动入库开关、SiYuan/Brain 目标勾选、目标状态和测试按钮；按钮改为调用后端测试 API，不再跳转页面。
- `tools/run_link_job.py` 支持 `storage_targets=[]` 或 `none/off/local_only` 表示不写任何外部 sink，避免自动入库关闭后回退到默认 SiYuan。
- 项目 skill `.agents/skills/link-intake-classifier/SKILL.md` 已沉淀同类排障规则，后续遇到存储测试或目标勾选问题先查 `/api/storage/test` 与 `storage_targets`。

### 验证

未运行真实链接处理、真实模型调用、OCR、评论抓取、SiYuan 写入或 Lucas Database 写入。

```powershell
python -B -m py_compile storage_config.py server\chat_api.py tools\run_link_job.py tests\test_storage_submit_api.py tests\test_chat_gateway.py
python -B -m unittest tests.test_storage_submit_api -v
python -B -m unittest tests.test_chat_gateway.ChatGatewayTests.test_storage_targets_support_testing_default_both_sinks tests.test_chat_gateway.ChatGatewayTests.test_storage_targets_can_disable_external_sinks tests.test_chat_gateway.ChatGatewayTests.test_ui_route_returns_minimal_chat_page -v
python -B -m unittest tests.test_chat_gateway -v
python -B -m unittest tests.test_lucas_database_sink -v
node -e "const fs=require('fs'); const html=fs.readFileSync('ui/minimal_chat/index.html','utf8'); const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]); for (const script of scripts) new Function(script); console.log(scripts.length + ' script(s) OK');"
```

结果：

- Python 编译通过。
- `tests.test_storage_submit_api` 9 tests OK。
- storage/UI 窄测 3 tests OK。
- `tests.test_chat_gateway` 35 tests OK。
- `tests.test_lucas_database_sink` 10 tests OK。
- 前端内联脚本解析 1 script OK。
- 本机非写入 HTTP 探测：SiYuan `ok=true`、`stage=notebook_list`、`write_attempted=false`；Brain 当前缺少 `LUCAS_DB_API_KEY`，返回 `status=missing_token`、`write_attempted=false`。
- 浏览器验证：`http://127.0.0.1:3964/ui` 存储抽屉可打开，点击 SiYuan/Brain 测试按钮后 URL 不变，目标卡状态会更新。

### 后续规则

- UI 的存储测试按钮必须走 `/api/storage/test`，不能打开 Web URL 或直接提交测试卡。
- 保存写入目标必须同步 `storage_targets`，否则 UI 勾选和下一次链接入库结果会不一致。
- Brain 的连通性测试不能把 `OPTIONS` 当成 token 验证；应使用 schema probe 区分 reachable、auth failed 和 schema rejected。

## 2026-07-03 普通 agent 对存储/模型配置问题自行猜测

### 问题

Lucas 在 `/ui` 询问“我现在可以写入的知识库有哪些”时，agent 回复“无法直接查询或列出所有可写入知识库”，随后按常见知识库形态猜测个人笔记库、主题库、临时队列等。这会误导用户，因为当前系统已经有存储配置、自动入库目标和 key 状态，agent 应该读取配置事实而不是猜。

### 原因

普通无链接消息走 `fallback_handler -> ChatResponder -> 当前聊天模型`。`ChatResponder` 只把最近对话和当前问题交给模型，没有注入 AI provider、Storage provider、`storage_targets`、自动入库勾选、key 是否存在等只读配置事实；也没有对配置类问题做确定性回答。存储 provider 列表也只有 Brain 和 SiYuan，缺少自定义 HTTP / Brain-compatible endpoint。

### 修复

- `storage_config.py` 新增 `custom_http` 存储 provider，支持保存 Base URL、Endpoint 和 `CUSTOM_STORAGE_API_KEY`。
- `/api/storage/config` 返回 `target_options`、`enabled_targets`、`writable_targets`，由后端统一判断“已勾选 / key 是否存在 / 最后能否写入”。
- `resolve_lucas_database_runtime()` 在 active storage provider 为 `custom_http` 时使用自定义 provider 作为 Brain-compatible runtime。
- `ChatResponder` 对存储配置问题和 AI 模型配置问题走确定性回答，不调用模型；普通模型问答 prompt 也注入只读配置事实，要求模型以配置为准，不得猜测。
- `fallback_handler` 透传 `data.storage` / `data.ai_config`，方便前端未来结构化渲染。
- `/ui` 存储配置加入 Endpoint 输入框、默认自定义 provider，目标卡片显示后端返回的可写状态。
- `.agents/skills/link-intake-classifier/SKILL.md` 新增聊天配置事实边界：配置类问题必须读本地配置，读不到就明确说明，不得猜。

### 验证

未运行真实链接、真实模型、OCR、评论抓取、SiYuan 写入或 Brain 写入；只做静态检查和单元测试。

```powershell
python -B -m py_compile storage_config.py ai_layer\chat_responder.py ai_layer\prompt_registry.py chat_gateway\handlers\fallback_handler.py tests\test_ai_layer.py tests\test_chat_gateway.py tests\test_storage_submit_api.py server\chat_api.py
python -B -m unittest tests.test_ai_layer tests.test_chat_gateway tests.test_storage_submit_api -v
node -e "<parse ui/minimal_chat/index.html inline scripts>"
```

结果：

- Python 编译通过。
- `tests.test_ai_layer`、`tests.test_chat_gateway`、`tests.test_storage_submit_api` 共 80 tests OK。
- `/ui` 内联脚本解析通过，1 script OK。
- 测试确认“我现在可以写入的知识库有哪些”返回 `storage_config_query`，`model_called=false`，并按配置列出 SiYuan 与 Lucas Database / Brain 的可写状态。

### 后续规则

- 用户问当前模型、provider、存储配置、自动入库目标、可写知识库或数据库时，agent 必须先读后端配置事实；不能用“通常可能包括”之类话术猜。
- 配置回答不得泄露 API key/token，只能报告 key 是否存在和对应 env 名。
- 自定义存储 provider 只代表 HTTP / Brain-compatible endpoint 配置；是否进入真实主链路仍由 `storage_targets` 和对应 Storage Sink 控制。

## 2026-07-03 聊天回复前缺少配置提醒

### 问题

Lucas 反馈用户在得到回复前，如果模型 API Key、SiYuan Token 或 Lucas Database API Key 等关键设置未配置，系统应该主动提醒，而不是让用户等到聊天失败、写卡失败或数据库写入失败后自行猜测原因。

### 原因

配置状态分散在 AI Layer 配置、Storage 配置和 link pipeline 配置里。Chat Gateway 返回 `reply_text` 时没有统一读取这些只读状态，也没有把缺项放进 `HandlerResponse.data`，所以前端只能看到最终业务失败，无法提前展示“缺模型 key / 缺存储 token / 写入目标未启用”这类可执行提示。

### 修复

- 新增 `chat_gateway/config_preflight.py`，统一执行只读配置预检，检查 AI provider 的 API Key / Base URL / model，以及当前 `storage_targets` 中 SiYuan、Lucas Database 的 token 和 Base URL 是否存在。
- `chat_gateway/message_router.py` 在链接和普通 fallback 响应后附加 `data.config_preflight` 与 `data.config_warnings`；链接响应还会把可见 warning 前置到 `reply_text` 的“配置提醒”段。
- `server/chat_api.py` 新增只读 `GET /api/system/status`，返回 API、runtime、AI、storage、capabilities 和 `config_warnings`；异步 batch 响应也复用同一套预检提醒。
- 文档同步更新 `docs/API_CONTRACT_V1.md` 和 `docs/APP_INTERACTION_MAP.md`，要求前端展示 `config_warnings`，并明确该预检不测试 provider 连接、不调用模型、不写入。

### 验证

未运行真实链接处理、真实模型调用、OCR、评论抓取、SiYuan 写入或 Lucas Database 写入。

```powershell
python -B -m py_compile chat_gateway\config_preflight.py chat_gateway\message_router.py server\chat_api.py tests\test_chat_gateway.py
python -B -m unittest tests.test_chat_gateway.ChatGatewayTests.test_link_reply_includes_config_warnings_before_user_reply tests.test_chat_gateway.ChatGatewayTests.test_system_status_reports_config_warnings_without_live_probe tests.test_chat_gateway.ChatGatewayTests.test_http_api_returns_handler_response -v
python -B -m unittest tests.test_chat_gateway -v
rg -n "config_warnings|config_preflight|GET /api/system/status" docs/API_CONTRACT_V1.md docs/APP_INTERACTION_MAP.md
rg -n "storage\.provider_id|近期必须实现.*api/system/status" docs/API_CONTRACT_V1.md docs/APP_INTERACTION_MAP.md
```

结果：

- Python 编译通过。
- 3 个配置提醒窄测 OK。
- `tests.test_chat_gateway` 37 tests OK。
- 文档静态检查确认契约/交互图包含 `config_warnings`、`config_preflight`、`GET /api/system/status`；未发现旧的 `storage.provider_id` 或把 `/api/system/status` 继续标为近期必须实现。

### 后续规则

- 面向用户的聊天回复如果依赖模型或写入目标，应携带安全的配置缺项提醒；不要把缺 key、缺 token 或未启用写入目标伪装成普通业务失败。
- 配置预检只能返回 `api_key_present`、`api_key_env`、Base URL 是否存在等安全状态，不能返回完整密钥。
- 只读预检不等于连接测试；模型连接和存储连接仍应由独立测试接口触发。

## 2026-07-03 `/ui` 链接任务进度只在开始和完成时刷新

### 问题

Lucas 反馈 `/ui` 的“项目进度”不是实时的：任务开始时前端只动一下，随后长时间停在同一状态，直到后端同步请求完整结束后才一次性显示完成。用户无法看到当前到底处于内容抓取、转写/OCR、生成知识卡还是写入数据库阶段。

### 原因

旧的 `/api/chat/messages` 是同步 HTTP：请求会等待 `handle_chat_payload -> link_handler -> tools/run_link_job.py` 完整结束才返回。前端只能先生成一个本地预测队列，等最终 `data.queue` 返回后整体替换，因此没有真实阶段级进度。

`tools/run_link_job.py` 本身已经持续写 `runtime/jobs/<job_id>/status.json`，但前端没有后台 batch id，也没有轮询接口把这些 `status.json` 阶段传回 UI。

### 修复

- `server/chat_api.py` 新增 `POST /api/chat/messages/async`：非 dry-run 链接任务会立即创建内存 batch，后台线程启动固定参数 `run_link_job.py` 子进程，接口立刻返回 `batch_id` 和初始队列。
- `server/chat_api.py` 新增 `GET /api/chat/batches/{batch_id}`：返回当前 batch 的 `data.queue`，每个 item 带 `stage`、`stage_label`、`stage_index`、`progress` 和四阶段 `stages`。
- 后台线程通过扫描 `runtime/jobs` 中当前任务的 `input.json/status.json` 绑定 job，再复用 `status.json` 的阶段更新；没有修改 `tools/run_link_job.py`。
- `/ui` 链接任务优先调用异步接口，拿到 `batch_id` 后轮询 batch 状态，并在任务行状态处显示后端阶段标签。
- dry-run 和普通聊天仍走原同步语义，不启动真实 runner。

### 验证

未运行真实链接处理、OCR、评论抓取、模型写卡、SiYuan 写入或 Brain 写入。HTTP 验证使用 dry-run；异步 runner 测试用 mock 阻止真实任务启动。

```powershell
python -B -m py_compile server\chat_api.py tests\test_chat_gateway.py
node -e "<parse ui/minimal_chat/index.html inline script>"
python -B -m unittest tests.test_chat_gateway -v
Invoke-RestMethod http://127.0.0.1:3964/health
Invoke-WebRequest http://127.0.0.1:3964/ui
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:3964/api/chat/messages/async -Body "<dry-run link payload>"
```

结果：

- Python 编译通过。
- `/ui` 内联脚本解析通过，`JS_PARSE_OK`。
- `tests.test_chat_gateway` 35 tests OK。
- 临时验证服务 `127.0.0.1:3964` 的 `/health` 正常。
- `/ui` 包含 `/api/chat/messages/async`、`pollRealtimeBatch`、`stage_label`。
- dry-run 调用异步入口返回 `data.async=false`、`runner_called=false`，未启动真实任务。

### 后续规则

- 真实阶段级 UI 不能依赖同步 HTTP 最终回包；应使用后台 job store + 轮询、SSE 或 WebSocket。
- 当前 batch store 是进程内存态，适合本机 UI 实时显示；服务重启后应回落到 `runtime/jobs` 的 job 详情查询。若要跨窗口/跨设备稳定恢复，后续应把 batch store 持久化。
- 前端阶段标签应来自后端 job 状态，不要再只用本地预测态冒充实时进度。

## 2026-07-02 `/ui` 修订回复显示原始 Markdown 且没有真实修订队列

### 问题

Lucas 反馈 `/ui` 里 agent 回复的 Markdown 没有被渲染，气泡里直接出现 `#`、`###`、`**` 等标记；同时“修改笔记”的回复只是聊天文字，没有真实队列或任务记录，用户无法判断到底有没有进入修订流程。

### 原因

前端消息气泡为了避免注入风险一直使用 `textContent` 渲染助手回复，因此模型返回的 Markdown 只会以纯文本显示。另一方面，`note_revision_request` 只返回 `data.revision` 协商上下文，没有本地 `runtime/jobs` 任务，也没有 `data.queue`；UI 收到顶层 `job_id` 或普通回复时也容易继续走 Brain receipt 查询语义，导致“修订”不像一个可追踪流程。

### 修复

- `/ui` 新增最小安全 Markdown renderer：先 HTML escape，再渲染标题、加粗、inline code、链接、有序/无序列表；助手气泡使用该 renderer，用户气泡继续用纯文本。
- `fallback_handler` 对 `note_revision_request` 创建本地 `runtime/jobs/revision-*` 任务目录，写入 `revision_intent.json`、`status.json` 和 `result.json`。
- 修订 job 返回 `data.revision_job` 和 `data.queue.items[0]`，其中 `item_type=note_revision`、`queue_status=in_progress`、`card_status=revision_intent_recorded`、`write_status=pending_policy`、`stages=["修订草稿","质量门禁","写入策略"]`。
- `/ui` 队列渲染识别 `item_type=note_revision`，显示“待写入策略”，并使用该 item 自带三阶段，不再套用链接入库四阶段。
- `/ui` 的 Brain receipt 刷新会跳过 `note_revision_request` / `data.revision_job`，显示“修订流程”，避免追加“Brain 未返回”或误导用户以为已经写入数据库。

### 验证

未运行真实链接处理、OCR、评论抓取、SiYuan 写入或 Brain 写入。修订 HTTP 验证会调用当前聊天模型，但只创建本地 `revision-*` 任务记录。

```powershell
python -B -m py_compile chat_gateway\handlers\fallback_handler.py tests\test_chat_gateway.py
python -B -m unittest tests.test_ai_layer.AILayerTests.test_chat_responder_note_revision_request_uses_model_reply tests.test_ai_layer.AILayerTests.test_chat_responder_revision_followup_uses_recent_card_context tests.test_ai_layer.AILayerTests.test_chat_responder_revision_model_refusal_uses_short_fallback tests.test_chat_gateway.ChatGatewayTests.test_note_revision_request_goes_to_model_backed_discussion tests.test_chat_gateway.ChatGatewayTests.test_revision_followup_with_recent_card_context_uses_model_backed_discussion tests.test_chat_gateway.ChatGatewayTests.test_ui_task_queue_uses_per_item_progress_and_prunes_completed_items -v
python -B -m unittest tests.test_ai_layer tests.test_chat_gateway -v
node -e "<parse ui/minimal_chat/index.html inline scripts>"
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:3963/api/chat/messages -ContentType "application/json; charset=utf-8" -Body "<note revision payload>"
```

结果：

- Python 编译通过。
- 相关修订/队列/UI 窄测通过。
- `tests.test_ai_layer` + `tests.test_chat_gateway` 共 64 tests OK。
- `/ui` 内联脚本解析通过，1 script OK。
- HTTP 修订请求返回 `status=note_revision_request`、`job_id=revision-*`、`data.revision_job.status=pending_quality_gate`、`data.queue.items[0].write_status=pending_policy`；真实模型回复能自然承接“直接删掉”这类修改意见。

### 后续规则

- 面向用户的助手 Markdown 回复应渲染成可读气泡，但 renderer 必须先 escape HTML，不能直接信任模型输出。
- “修改已入库笔记”应产生可追踪的修订意图 job 和队列状态；除非后续受控 revision sink 真正完成写入，不得声称已有 SiYuan / Brain 笔记已经被覆盖、删除或更新。
- 修订队列不是链接入库队列，也不是 Brain 写入回执；前端应单独展示其状态。

## 2026-07-02 `/ui` 队列总体进度误作单任务进度

### 问题

Lucas 反馈 `/ui` 队列面板左侧四个阶段块本质上属于单个任务，却被界面画成了所有项目的总体阶段；任务完成后仍留在队列里；顶部进度条显示的是整体项目进度，而不是每个任务独立进度。随后又指出普通聊天也会拉起队列，这是错误的，队列只应在链接导入、附件导入或文本明确要求写入知识库时出现。

### 原因

前端 `renderTaskQueue()` 使用队列聚合态计算一个全局 `progress` 和 `activeStage`，再把 `内容抓取 -> 转写 / OCR -> 生成知识卡 -> 写入数据库` 四阶段渲染成全局 stage list。`combinedTaskQueueState()` 也会保留已完成 item，所以完成任务仍显示在队列中。顶部进度条因此表达的是“队列整体完成比例”，不是单个任务状态。

另外，`extractTaskTargets()` 对任意非空文本都会创建 `kind=message` 的本地任务，导致普通聊天消息也弹出队列。

### 修复

- `/ui` 队列详情改为只显示活动任务列表，不再渲染全局四阶段块。
- 每个任务行新增独立进度条和四阶段小刻度。
- `combinedTaskQueueState()` 过滤 `queue_status=completed` 的 item，完成任务自动退出活动队列。
- 完成后的空队列会自动收起，队列按钮计数只表示当前活动任务数。
- 顶部不再显示整体进度条和“已完成”汇总文案，只保留当前处理/排队/失败数量。
- `extractTaskTargets()` 不再为普通文本创建队列任务；只有 URL、附件，或文本命中明确知识入库意图时才创建任务。
- 新增 `textHasKnowledgeIntakeIntent()`，识别“入库、写入知识库、保存到知识库、生成知识卡、写入 Brain/思源”等明确写入表达。

### 验证

已执行静态/单元验证，未运行真实链接、模型、OCR、评论抓取、SiYuan 写入或 Brain 写入。

```powershell
python -B -m py_compile tests\test_chat_gateway.py server\chat_api.py
node -e "<parse ui/minimal_chat/index.html inline scripts>"
python -B -m unittest tests.test_chat_gateway.ChatGatewayTests.test_ui_task_queue_uses_per_item_progress_and_prunes_completed_items tests.test_chat_gateway.ChatGatewayTests.test_ui_route_returns_minimal_chat_page -v
python -B -m unittest tests.test_chat_gateway -v
```

结果：

- Python 编译通过。
- 前端内联脚本解析通过，2 scripts OK。
- 新增 UI 队列测试通过。
- 完整 `tests.test_chat_gateway` 32 tests OK。
- 本机 `127.0.0.1:3963` 已重启，`/health` 正常；HTTP 检查确认 `/ui` 已包含 `task-item-progress` / `task-item-stages` / `textHasKnowledgeIntakeIntent`，且不再包含旧的全局阶段渲染、“已完成 ·”汇总文案或 `kind: 'message'` 普通文本队列任务。

### 后续规则

- 队列面板里的阶段进度默认属于单个任务，不应再用队列聚合状态驱动四阶段 UI。
- 完成态任务应从活动队列退出；如需历史记录，应单独做“已完成任务/历史”视图，而不是混进运行队列。
- 普通聊天不应触发任务队列；只有明确 intake/write intent 或外部内容导入才进入队列。
- 真实阶段进度仍需后台 job store 或轮询/SSE/WebSocket 支持；当前前端本地进度只是发送后到回执前的预测态。

## 2026-07-02 已入库笔记修订请求被误导为手动编辑且复述乱码路径

### 问题

Lucas 在 `/ui` 对刚入库的笔记说“我需要调整笔记”时，agent 回复成“只能处理新消息，无法直接修改已有笔记”，并把历史里的存储路径复述成带 `�` 的乱码。用户真正需要的是先和 agent 讨论不满意之处，让后续修改 agent 能据此生成修订版或走受控写入。

### 原因

普通无链接消息都走 `fallback_handler -> ChatResponder -> 模型`，没有单独识别“已入库笔记修订请求”。模型只能从最近对话和宽泛能力边界自行推断，于是把用户推向手动编辑，并可能复述旧历史里的路径内容。前端本地会话恢复和发送 `conversation_history` 时也没有过滤已经损坏的 `�` 行或存储路径行，坏历史会继续进入模型上下文。

### 修复

- `IntentClassifier` 新增 `note_revision_request` 确定性意图，识别“调整/修改/不满意 + 笔记/知识卡/卡片”等表达。
- `ChatResponder` 对该意图会调用当前聊天模型生成自然回复，不再使用固定 checklist 模板；如果模型输出“不能调用后端/不能写入知识库”这类拒绝式话术，才用短兜底替换。
- `ChatResponder` 额外识别有最近卡片上下文的跟进表达，例如“重新提交 / 帮我修改 Crow5...”，避免这类话再次落回普通模型闲聊。
- `fallback_handler` 在 `data.revision` 返回 `requested/mode/candidate_title/requested_fields`，供后续真正的笔记修改 agent 接入。
- `ChatResponder` 和 `/ui` 发送历史时过滤存储路径行与含 `�` 的乱码行，避免坏路径被继续喂给模型。
- `chat_responder_system_prompt` 补充规则：遇到已入库笔记不满意，应先澄清修订目标，不输出或复述存储路径。

### 验证

未运行真实链接任务、模型调用、OCR、评论抓取、SiYuan 写入或 Brain 写入。

```powershell
python -B -m py_compile ai_layer\intent_classifier.py ai_layer\chat_responder.py ai_layer\prompt_registry.py chat_gateway\handlers\fallback_handler.py tests\test_ai_layer.py tests\test_chat_gateway.py
python -B -m unittest tests.test_ai_layer.AILayerTests.test_chat_responder_note_revision_request_uses_model_reply tests.test_ai_layer.AILayerTests.test_chat_responder_revision_followup_uses_recent_card_context tests.test_ai_layer.AILayerTests.test_chat_responder_revision_model_refusal_uses_short_fallback tests.test_chat_gateway.ChatGatewayTests.test_note_revision_request_goes_to_model_backed_discussion tests.test_chat_gateway.ChatGatewayTests.test_revision_followup_with_recent_card_context_uses_model_backed_discussion -v
python -B -m unittest tests.test_ai_layer tests.test_chat_gateway -v
node -e "<parse ui/minimal_chat/index.html inline scripts>"
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:3963/api/chat/messages -ContentType "application/json; charset=utf-8" -Body "<note revision follow-up payload>"
```

结果：

- Python 编译通过。
- 5 个新增/相关窄测 OK。
- `tests.test_ai_layer` + `tests.test_chat_gateway` 共 64 tests OK。
- `/ui` 内联脚本解析通过。
- 当前工作区 Chat Gateway 已重启在默认 `127.0.0.1:3963`，`/health` 正常；HTTP 修订跟进请求返回 `status=note_revision_request`，`data.revision.requested=true`，能从“帮我修改 Crow5...”中识别候选标题。真实 DeepSeek 验证中 `model_called=true`、`model_result_ok=true`，回复自然承接“直接删掉”这类修改意见。

### 后续规则

- “修改既有笔记”不能伪装成已经写入，也不能让模型自由输出文件路径；应先形成可复盘修改意图，再由后续受控修改/写入链路执行。
- 自动化仍不得直接覆盖、删除、移动思源已有笔记；如需真正更新，必须单独设计 Storage Sink 的受控修订流程。

## 2026-07-02 `/ui` 任务不能并行与 OCR 阶段初始误亮

### 问题

Lucas 在 `/ui` 连续发布两个入库任务时，同一窗口和不同窗口都表现为不能并行；任务面板还会在刚开始时把“转写 / OCR”阶段高亮，视觉上像 OCR 已经默认完成。

### 原因

后端 Chat Gateway 多 URL 分支仍使用 `for` 循环逐个调用 `tools/run_link_job.py`，`data.queue.mode` 也沿用 `sequential` 语义。前端发送时会把发送按钮禁用到请求结束，并且只保存一个本地队列快照，第二个任务会覆盖第一个任务的队列展示。

OCR 初始误亮不是 OCR 真实完成，而是前端本地假进度用 `0.45 / total` 推断阶段。单任务一开始就得到 45% 进度，于是第一阶段显示完成、第二阶段“转写 / OCR”显示激活。

### 修复

- `chat_gateway/handlers/link_handler.py` 将多 URL 处理改为受限并发，默认最多 2 个 runner 并行，可通过 `LUCAS_LINK_HANDLER_MAX_PARALLEL_JOBS` 调整到 1-4。
- 多 URL 队列回执改为 `mode=parallel`，回复文案显示“并行队列”。
- `/ui` 前端发送后不再禁用发送按钮，允许同一窗口继续发布新任务。
- `/ui` 为本地任务加入 batch id，并合并多个 batch 展示，避免后发任务覆盖先发任务。
- `/ui` 初始本地进度改为第一阶段激活，不再用 45% 假进度把 OCR 阶段画亮。
- `docs/CHAT_GATEWAY_ABSTRACTION.md` 同步更新多 URL 语义。

### 验证

已执行静态/单元验证，未运行真实链接、模型、OCR、评论抓取、SiYuan 写入或 Brain 写入。

```powershell
python -B -m py_compile chat_gateway\handlers\link_handler.py tests\test_chat_gateway.py server\chat_api.py
python -B -m unittest tests.test_chat_gateway.ChatGatewayTests.test_multiple_urls_are_reported_as_pending_queue_in_dry_run tests.test_chat_gateway.ChatGatewayTests.test_multiple_urls_are_processed_in_parallel_with_card_status tests.test_chat_gateway.ChatGatewayTests.test_http_api_returns_handler_response tests.test_chat_gateway.ChatGatewayTests.test_http_api_accepts_metadata_dry_run tests.test_chat_gateway.ChatGatewayTests.test_ui_route_returns_minimal_chat_page -v
python -B -m unittest tests.test_chat_gateway -v
node -e "<parse ui/minimal_chat/index.html inline scripts>"
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:3963/api/chat/messages -ContentType application/json -Body "<dry-run two-url payload>"
```

结果：

- Python 编译通过。
- 5 个相关单测 OK，完整 `tests.test_chat_gateway` 30 tests OK，其中并发测试确认两个 `_run_link_job` 调用存在重叠执行。
- 前端内联脚本解析通过。
- 本机 `127.0.0.1:3963` 已重启，`/health` 正常。
- HTTP dry-run 两链接返回 `data.queue.mode=parallel`、`total=2`、`pending=2`、`runner_called=false`。

### 后续规则

- 当前仍是同步 HTTP 请求：接口会等本批次任务结束后返回最终队列快照，不是后台 worker 和实时状态推送。
- 如果要跨窗口共享全局运行队列、实时看到每个 job 的阶段变化，需要新增后台 job store 加轮询、SSE 或 WebSocket。
- 前端本地阶段只能表达“预测进度”，不能把未收到的 OCR/转写状态画成已完成。

## 2026-07-02 正式卡入库后仍大量进入待分类

### 问题

正式知识卡即使已经通过模型写卡和质量门禁，入库后仍容易堆到 `Inbox / 待分类`；部分内容只是提到 AI 或 AI 编程，就被分到不合适的 AI 类目，不能优先复用已有知识树节点。

### 原因

分类链路已有 `TaxonomyRouter`，但它主要依赖内置 seed taxonomy，没有读取 Lucas 当前知识库里的真实知识树。与此同时，SiYuan writer 固定使用 `inbox_path` 创建文档，即使 job 目录已经存在 `taxonomy_decision.json`，SiYuan 也不会消费该分类结果。

重复内容也缺少明确传递给 Brain 的来源/内容指纹，导致数据库端很难判断同一篇文章、同一条视频或同一段内容应该更新既有卡、跳过，还是标为近似重复复核。

### 修复

- `taxonomy_layer` 支持读取 `TaxonomyTreeV1` 外部快照，默认查找 `runtime/taxonomy_tree.json`，并可通过 `taxonomy_tree_path` 配置。
- 新增 `tools/export_siyuan_taxonomy_tree.py`，通过 SiYuan HTTP API 导出分类树快照；不依赖 MCP，不直接读写 SiYuan 工作空间文件。当前本机 `taxonomy_export_root_path=/`，因为“知识卡”是 notebook/UI 根而不是文档路径。
- `tools/run_link_job.py` 在正式卡通过质量门禁后、分类前可按 `taxonomy_refresh_from_siyuan=true` 刷新知识树快照；刷新失败只记录日志，不阻断入库。
- `tools/write_siyuan.py` 对正式卡优先使用 `taxonomy_decision.json` 写入 `<siyuan_taxonomy_root_path>/<recommended_path>/<title>`；当前本机前缀为空，非正式卡仍写 Inbox。
- `tools/write_siyuan.py` 对同一路径重复按 `siyuan_duplicate_policy=skip_exact_path` 跳过，不覆盖旧笔记。
- `tools/write_lucas_database.py` 在 payload 中新增 `dedupe`，包含规范化来源、来源 hash、标题 fingerprint 和内容 fingerprint，供 Brain 做 exact/near duplicate 取舍。

### 验证

已执行静态/单元验证，未运行真实链接、模型、OCR、评论抓取、SiYuan 写入或 Brain 写入。

```powershell
python -B -m py_compile taxonomy_layer\taxonomy_store.py taxonomy_layer\taxonomy_router.py tools\run_link_job.py tools\write_siyuan.py tools\write_lucas_database.py tools\export_siyuan_taxonomy_tree.py tests\test_taxonomy_router.py tests\test_siyuan_writer.py tests\test_lucas_database_sink.py
python -B -m unittest tests.test_taxonomy_router tests.test_siyuan_writer tests.test_lucas_database_sink -v
```

结果：

- `py_compile` 通过。
- taxonomy / SiYuan writer / Brain sink 相关单测 34 tests OK。
- 追加运行 `python -B -m unittest tests.test_chat_gateway -v`，29 tests OK。
- 追加运行 `python tools\export_siyuan_taxonomy_tree.py --help`，脚本启动和 import 正常。

### 后续规则

- 分类必须发生在正式 `ComposedCardV1` 和质量门禁之后，不能塞进 composer。
- 如果真实卡仍进 `Inbox / 待分类`，先检查 `taxonomy_decision.json` 的 `taxonomy_source`、`similar_categories` 和知识树快照，而不是直接降低阈值。
- 生产分类刷新只能走本地脚本/HTTP API/快照文件，不把 MCP 当作生产依赖。
- 对重复来源优先保留或更新既有记录；近似内容应标记复核，不能无脑新建一批相同卡。

## 2026-07-02 发送链接后弹出 Python 空控制台

### 问题

在 `/ui` 发送链接后，Windows 桌面会自动弹出一个标题为 `C:\Python311\python.exe` 的黑色空控制台窗口，干扰前端使用。

### 原因

Chat Gateway 的 link handler 收到链接后会通过 `subprocess.run([sys.executable, tools/run_link_job.py, ...])` 启动本地 runner。Windows 上当父进程以无控制台或隐藏方式运行时，未显式设置隐藏窗口的 console 子进程可能自己创建一个可见控制台窗口。

这个弹窗不是 MCP，也不是 SiYuan、Brain 或浏览器授权窗口；它是链接 runner 的子 Python 进程窗口。

### 修复

`chat_gateway/handlers/link_handler.py` 新增 `_runner_creationflags()`，在 Windows 下为 runner 子进程传入 `subprocess.CREATE_NO_WINDOW`，非 Windows 保持 `0`。原有参数数组、`shell=False`、stdout/stderr 捕获、timeout 和 runner 语义不变。

### 验证

已执行：

```powershell
python -B -m py_compile chat_gateway/handlers/link_handler.py tests/test_chat_gateway.py
python -B -m unittest tests.test_chat_gateway.ChatGatewayTests.test_link_runner_hides_child_python_console_on_windows -v
```

结果：

- 相关文件 `py_compile` 通过。
- 新增回归测试通过，确认 Windows 下调用 runner 时会传入 `CREATE_NO_WINDOW`。
- 已重启本机 `127.0.0.1:3963` Chat Gateway，使修复生效。

### 后续规则

- 后续新增 Windows 后台 runner 时，如果从无控制台服务进程启动 console 程序，应显式设置隐藏窗口或等价的 no-window 参数。
- 不要为了避免弹窗改成 `shell=True` 或拼接命令字符串；继续使用固定参数数组。

## 2026-07-02 DeepSeek 长附录触发质量门禁误伤

### 问题

最近两个抖音入库任务进入了模型复核卡：

- `runtime/jobs/20260702-172521-05eccc45`
- `runtime/jobs/20260702-173147-0cef002e`

两个任务的模型写卡均成功，模型提供方为 `deepseek_compatible` / `deepseek-chat`，但质量门禁失败：

- `quality_gate_passed=false`
- `failed_checks=["large_transcript_repeated_in_markdown"]`
- `final_card_type=temporary_review_card`

这导致它们没有按正式卡路径进入后续正式存储策略。

### 原因

根因不是 DeepSeek 调用失败，也不是转写失败。

DeepSeek 生成的结构化卡里，把较长的原始 transcript 放进了 `appendix_transcript_excerpt`。`tools/card_composer.py` 渲染 Markdown 时把该字段放到“原始材料摘录 / 附录”章节。旧版 `tools/card_quality_gate.py` 检查 transcript 重复时扫描整篇 Markdown，没有区分正文和附录，于是把“附录里的原始材料摘录”误判成正文大段照搬 transcript。

### 修复

本次修复分三层：

- Prompt 层：`ai_layer/card_composer_service.py` 和 `ai_layer/prompt_registry.py` 明确要求 `evidence_quotes`、`appendix_transcript_excerpt` 只放短摘录，不要连续复制 transcript。
- 结构化兜底层：`ai_layer/composed_card_schema.py` 和 `tools/card_composer.py` 对模型返回的证据摘录、附录摘录做长度截断，避免模型忽略提示时把大段原文渲染进卡片。
- 门禁层：`tools/card_quality_gate.py` 检查大段 transcript 重复前会剥离 `## 原始材料摘录 / 附录` 和旧的 `## 原始转写摘录 / 附录` 章节；如果重复只出现在附录，不再判失败，只记录 `large_transcript_repeated_in_appendix_ignored` warning。

仍然保留的保护：

- 正文、摘要、核心观点和知识块里大段照搬 transcript 仍会失败。
- composer 失败、fallback、材料不足仍不能冒充正式卡。
- 失败卡不能改标成 `formal_summary` 来绕过门禁。

### 验证

已执行：

```powershell
python -m unittest tests.test_ai_layer
python -B -m py_compile ai_layer/card_composer_service.py ai_layer/prompt_registry.py ai_layer/composed_card_schema.py tools/card_composer.py tools/card_quality_gate.py
```

结果：

- `tests.test_ai_layer` 26 tests OK。
- 相关文件 `py_compile` 通过。
- 对两个历史 job 使用新门禁做预览时均得到 `quality_gate_passed=true`、`failed_checks=[]`、`warnings=["large_transcript_repeated_in_appendix_ignored"]`、`final_card_type=formal_summary`。
- 历史生产记录没有被改写，预览文件已删除。

### 后续规则

- 以后遇到“定位问题并完成修复”的情况，详细复盘写入本文件。
- `docs/HANDOFF.md` 只写当前交接需要知道的简短指针，不承载完整问题复盘。
- 如果问题改变了生产规则，还要同步更新对应规则文档，例如 intake 问题更新 `docs/INTAKE_SYSTEM_RULES.md`，API/UI 边界问题更新相应契约文档。

## 2026-07-02 Chat Gateway 入库回复只显示状态不显示摘要

### 问题

Lucas 反馈聊天入口收到链接后的 agent 回复过于像技术回执：只展示“已入库、标题、等级、状态、路径、评论数”等字段。用户真正需要先看到“这个内容讲什么、什么值得学”，再附一个简短入库卡片，说明文件树、标题、一句话摘要和是否成功接入即可。

截图中还暴露了长路径或异常编码路径会占据主气泡，降低可读性。

### 原因

`chat_gateway/handlers/link_handler.py` 的 `_build_reply()` 只消费 `result.json` / `write_result` 的存储元信息，没有读取当前 job 已生成的 `composed_card.json` 和 `taxonomy_decision.json`。因此模型已经产出的 `one_sentence_summary`、`reusable_value`、`application_suggestions` 没有进入最终用户回复。

### 修复

- `link_handler.py` 新增回复卡片提取逻辑，从当前 job 的 `composed_card.json`、`taxonomy_decision.json`、`write_result` 和 Lucas Database 写入结果中兜底生成 `reply_card`。
- 单链接成功回复改为“摘要 / 值得学习 / 入库卡片”，卡片只展示标题、文件树、一句话摘要、是否成功接入。
- `data.result.reply_card` 同步返回结构化字段，便于前端后续渲染真实卡片组件。
- 对临时卡、来源材料卡、复核卡继续标注为“是（临时卡，待复核）”，避免把非正式卡误说成正式知识卡。

### 验证

未运行真实链接、模型、OCR、评论抓取、SiYuan 或 Brain 写入。已执行 mock runner 单元测试：

```powershell
python -B -m unittest tests.test_chat_gateway.ChatGatewayTests.test_single_link_reply_shows_summary_learning_and_storage_card tests.test_chat_gateway.ChatGatewayTests.test_multiple_urls_are_reported_as_pending_queue_in_dry_run tests.test_chat_gateway.ChatGatewayTests.test_multiple_urls_are_processed_in_parallel_with_card_status -v
python -B -m unittest tests.test_chat_gateway -v
```

结果：

- 窄范围 3 tests OK。
- `tests.test_chat_gateway` 30 tests OK。

### 后续规则

- 面向聊天用户的 `reply_text` 应优先回答“讲什么、学什么”，不要把内部等级、评论数、裸本地路径放在主回复第一层。
- 存储路径优先展示分类文件树；如果写入路径包含异常编码，应回退到 taxonomy decision 或结构化写入结果。
- 技术细节继续放在 `data.result`、`data.queue` 和 job artifacts，供 UI 调试面板使用。

## 2026-07-03 完成回执缺少数据库目标、写入层级和摘要

### 问题

Lucas 反馈 `/ui` 完成后的助手回执又退化成队列状态，只显示“已检测到 1 个链接 / 队列状态 / 标题 / job_id”，没有明确写入了哪个数据库、写入到数据库的哪个层级，也没有展示模型写出的摘要。

现场 job `runtime/jobs/20260703-185606-bb48a6b1` 实际已经有完整结果：`composed_card.json` 里有摘要，`write_result.json` / `result.json.write_result` 有 SiYuan 路径，`lucas_database_write_results` 里有主数据库成功回执和数据库 2 的失败原因。因此这不是写入链路没产物，而是完成回执展示层丢字段。

### 原因

`server/chat_api.py` 的异步单链接完成态仍使用 `_build_queue_reply()` 生成 `reply_text`。这个模板适合多链接队列状态，但对单链接完成结果太瘦，只保留队列统计、标题和 `job_id`。同时 `chat_gateway/handlers/link_handler.py` 的 `reply_card` 只展示 `file_tree`，没有显式返回 storage targets / storage layers，导致 UI 即使拿到结构化结果，也无法直接展示“哪个数据库 / 哪个层级”。

### 修复

- `chat_gateway/handlers/link_handler.py` 新增 storage location 提取逻辑，把 SiYuan 与 Lucas Database / Brain 的成功或失败回执统一整理为 `reply_card.storage_locations`。
- `reply_card` 新增 `storage_targets` 和 `storage_layers`，成功回复正文新增“写入目标 / 写入层级”两行。
- `server/chat_api.py` 新增 `_build_async_batch_reply()`：单链接异步任务完成后优先使用 item 上的完整结果卡 `reply_text`；多链接或未完成任务继续使用队列模板。
- 异步 worker 完成 item 时保存 `reply_text=link_handler._build_reply(result)`，避免最终轮询响应重新压扁成队列摘要。

### 验证

未运行真实链接、真实模型、OCR、评论抓取、SiYuan 或 Brain 写入。使用已有 job 只读预览和 mock 单元测试验证。

已执行：

```powershell
python -B -m py_compile chat_gateway\handlers\link_handler.py server\chat_api.py tests\test_chat_gateway.py
python -B -m unittest tests.test_chat_gateway.ChatGatewayTests.test_single_link_reply_shows_summary_learning_and_storage_card tests.test_chat_gateway.ChatGatewayTests.test_async_single_link_completed_reply_uses_result_card_not_queue_template tests.test_chat_gateway.ChatGatewayTests.test_http_api_async_link_returns_realtime_batch_without_running_real_task -v
python -B -m unittest tests.test_chat_gateway -v
```

结果：

- 相关文件 `py_compile` 通过。
- 窄范围 3 tests OK。
- `tests.test_chat_gateway` 48 tests OK。
- 对历史 job `20260703-185606-bb48a6b1` 的只读预览现在显示：摘要、`写入目标：SiYuan、主数据库；数据库 2 未写入（404 page not found）`、`写入层级：SiYuan：/AI/内容生产/分发策略；主数据库：/知识卡/AI/内容生产/分发策略`。
- 已重启本机 `127.0.0.1:3963` Chat Gateway，新 PID 为 `93628`，`/health` 正常。

### 后续规则

- 单链接完成态不能使用多链接队列模板作为最终用户回执。
- 用户可见完成卡必须至少能说明摘要、写入目标、写入层级和标题。
- 多数据库写入存在部分失败时，回执要同时展示成功目标和失败目标原因，不能只说总体“完成”。

## 2026-07-03 正式卡材料充足但因缺分类落入待分类

### 问题

Lucas 指出真实 job `20260703-203243-d55869bf` 已成功读取小红书页面材料并通过质量门禁，但 SiYuan 和 Brain 都写到了 `Inbox / 待分类`。用户期望是：材料已经获取到并通过门禁就应写入；如果知识树里没有现成分类，应创建或使用相关分类，而不是堆到待分类。

### 原因

该 job 的 `taxonomy_decision.json` 已识别出 `CategoryCreateProposalV1`，proposal 为 `["职场"]`，但 `CategoryPolicy.allows_auto_create_from_proposal()` 只允许 `法律`、`项目` 和受控 AI 路径自动使用 proposal。由于 `["职场"]` 不在允许范围内，即使 `taxonomy_auto_create_from_proposal=true`，最终仍回退到 `Inbox / 待分类`。

这不是登录授权失败：job 读到了标题、作者、正文、互动指标和视频源，并且 `material_quality.can_compose_formal=true`、`quality_gate_passed=true`。

### 修复

- `taxonomy_layer/category_policy.py`：允许明确的一层通用主题在自动 proposal 开启时作为写入目标，例如 `职场`。
- 后续修订：`生活方式 / 穿搭`、`生活方式 / 防晒` 等清晰生活方式 proposal 已允许在自动 proposal 开启时写入；`AI / 内容生产 / <主题>` 仍必须有明确 AI 内容生产证据；`小红书`、`幽默`、`内容` 等泛标签不会自动建类。
- `tests/test_taxonomy_router.py`：新增职场卡启用/未启用自动 proposal 的回归测试。
- 同步更新 `docs/INTAKE_SYSTEM_RULES.md`、`docs/TAXONOMY_ROUTER_V1.md` 和 intake skill。

### 验证

已执行：

```powershell
python -B -m unittest tests.test_taxonomy_router -v
```

结果：26 tests OK。用历史 job 的 `composed_card.json` 只读重算，新的 taxonomy decision 为 `recommended_path=["职场"]`、`confidence="medium"`、`fallback=null`。对应 Brain 目标路径为 `/知识卡/职场/上海人入职说真话真诚在职场横着走`，SiYuan 文件夹为 `职场`。

### 后续规则

- 正式卡已经有足够材料并通过质量门禁时，缺少既有分类不应默认导致待分类；如果配置启用自动 proposal 且主题清晰，应写入 proposal 路径。
- 排查“为什么待分类”时，先看 `taxonomy_decision.create_proposal`、`taxonomy_auto_create_from_proposal` 和 `allows_auto_create_from_proposal()`，再判断是否需要补分类别名或保护规则。

## 2026-07-03 视觉平台文本足够时跳过 OCR 和结构化互动指标

### 问题

Lucas 截图反馈小红书卡片再次出现“未提取到画面文字证据”，并且 Brain 的“评论与互动信号”显示点赞、评论、收藏、分享均未读取到。用户指出：文本信息够写卡不代表可以跳过 OCR、图片、视频和互动补全；正确策略应是材料不足时 OCR 多一点，材料足够时也要做补全。

现场 job `20260703-205702-3a6de92d` 已读到 `has_video=true`、`image_count=180`、`video_source_count=3`、`need_ocr=true`，且页面指标有点赞 16000、评论 724、收藏 1831、分享 8953。但 `ocr.json.status=skipped_pending_media_ocr`，`source_material.ocr_evidence_items=[]`，Brain ingest 的 `source_material` 结构里也没有互动指标字段。

### 原因

`tools/run_link_job.py` 的视觉平台网页图片 OCR 兜底被 `material_quality.can_compose_formal=true` 截断。也就是说，只要页面标题、正文和标签已经足够让模型写正式卡，就不会再进入 `ocr_web_images.py`，即使 reader 已明确标记 `need_ocr=true`。

另一个问题在 Brain payload：`tools/card_composer.py` 渲染出的 Markdown 已包含平台互动指标，但 `tools/write_lucas_database.py` 只把评论样本写进 `source_material.comments`，没有把平台点赞 / 评论 / 收藏 / 分享写入结构化 `source_material`。Brain 若按结构化材料渲染，就会显示“未读取到”。

后续 Lucas 发送典型视频笔记 job `20260703-212837-42bbb3f9` 后又暴露出第三个问题：新逻辑已经触发 `web_image_ocr_running`，但 `ocr_web_images.py` 返回 `web_image_ocr_no_images`。根因是 CDP 捕获器只枚举 `document.images`，没有把可见 `<video>`、`<canvas>` 或 CSS `background-image` 当作可截图候选；所以纯视频笔记即使有 `has_video=true` 和 `video_source_count=3`，也不会留下相关画面图片。

### 修复

- `tools/run_link_job.py` 新增 `has_visual_capture_candidate()`，并调整 `should_run_visual_platform_web_image_ocr()`：视觉平台在 `need_ocr=true` 且有视频、图片或视频源时，即使 `can_compose_formal=true` 也会运行网页图片 / 媒体截图 OCR 补全。
- `web_image_ocr_running.reason` 新增 `visual_platform_ocr_requested`，区分“材料不足兜底”和“材料已够但视觉补全”。
- `tools/write_lucas_database.py` 将平台互动指标写入 `source_material.engagement`，同时保留顶层 `like_count/comment_count/collect_count/share_count/metrics_*` 和 `metadata.platform_engagement` 兼容字段。
- `tools/card_composer.py` 将评论区文案改成“评论样本数量 / 评论样本来源”，避免把平台评论数冒充成已采集的评论样本数。
- `tools/ocr_web_images.py` 的 CDP 候选采集扩展到可见 `<video>`、`<canvas>` 和 CSS `background-image`；视频 / 画布候选通过元素区域截图保存，`capture_method` 会记录为 `video_element_screenshot` 或 `canvas_element_screenshot`。
- 新增回归测试覆盖“视觉平台材料已够但仍需 OCR 补全”和“评论样本为空时平台互动指标不能丢”。

### 验证

未运行真实链接、真实 OCR、真实模型、评论抓取、SiYuan 写入或 Brain 写入。

已执行：

```powershell
python -B -m py_compile tools\run_link_job.py tools\write_lucas_database.py tools\card_composer.py tests\test_chat_gateway.py tests\test_lucas_database_sink.py
python -B -m unittest tests.test_chat_gateway -v
python -B -m unittest tests.test_lucas_database_sink -v
python -B -m unittest tests.test_ocr_material.OCRMaterialTests.test_card_composer_reads_ocr_material_v1_as_visual_evidence tests.test_ocr_material.OCRMaterialTests.test_card_composer_skips_ordinary_spoken_subtitle_frames tests.test_ocr_material.OCRMaterialTests.test_card_composer_renders_sampled_images_when_ocr_text_is_empty -v
node --check %TEMP%\ocr_web_images_cdp_check.js
```

结果：

- `tests.test_chat_gateway` 54 tests OK。
- `tests.test_lucas_database_sink` 11 tests OK。
- `tests.test_ocr_material` 28 tests OK。
- `ocr_web_images.py` 内嵌 CDP 脚本 Node 语法检查通过。
- 对旧 job `20260703-205702-3a6de92d` 只构建 payload 验证：`source_material.engagement`、顶层 metrics 和 `metadata.platform_engagement` 均包含 `16000/724/1831/8953`；`metadata.comments_count` 仍为 `0`，表示评论样本未采集。
- 本地 `file://` video poster 烟测通过：`ocr_web_images.py` 捕获 `video_element_screenshot`，写入 `sampling.frames[0]`，RapidOCR 读到测试文字。

### 后续规则

- `MaterialQualityV1.can_compose_formal` 只能决定是否能写正式卡，不能决定是否跳过 OCR、图片、视频、互动指标等增强材料。
- 视觉平台材料不足时应尽量用 OCR / 图片采样补主材料；材料足够时仍应做轻量视觉补全，防止卡片长期显示 `skipped_pending_media_ocr`。
- 平台评论数和评论样本数必须分开：平台评论数是互动指标，评论样本数是实际抓取的评论条数，不能相互覆盖。
