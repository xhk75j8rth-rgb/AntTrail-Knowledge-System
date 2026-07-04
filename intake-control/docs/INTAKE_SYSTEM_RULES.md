# Intake System Rules

## Mission

本项目是个人知识库内容入库中控系统。它统一接收聊天消息、链接、截图文字、视频、GPT/Claude/DeepSeek/Codex 对话、普通文本和未来自研聊天软件输入，自动识别内容类型，尽可能完整读取来源，采集结构化材料，调用模型写卡，通过质量门禁后写入 SiYuan 或未来 Storage Sink。

它不是单纯链接总结工具，也不是 MCP 工具集合。

## MCP Debug-Only Policy

MCP 可以存在，但只能是 debug-only / interactive-only 能力。

生产自动化契约只认本地 Python 脚本、HTTP API、固定参数子进程、Storage Sink 和 `runtime/jobs` 可复盘文件。任何正式 intake、转写、OCR、评论、模型写卡、质量门禁或写入链路，都不得要求 MCP 授权弹窗、Playwright MCP、SiYuan MCP 或 Codex MCP 才能运行。

允许 MCP 的场景：

- Codex 人工探索页面状态；
- 开发期临时排查浏览器、SiYuan 或外部工具状态；
- 对照验证一个问题是否来自页面、账号状态或本地环境；
- 不进入生产承诺的手动辅助操作。

不允许 MCP 的场景：

- 作为 Chat Gateway、微信桥、自研聊天软件或 debug CLI 的后台依赖；
- 作为 `run_link_job.py`、转写、OCR、评论抓取、写卡、质量门禁或写入的必需步骤；
- 写入生产文档、API 契约或能力说明时暗示 MCP 是稳定后台服务；
- 为了简化架构直接删除 MCP，而没有先确认其调试价值、引用关系和 Python/HTTP 替代路径。

## Stable Capabilities

- Chat Gateway 已支持标准 `MessageEvent` 和 `HandlerResponse`。
- 微信桥、自研聊天 HTTP API、debug CLI 都是 adapter。
- `tools/run_link_job.py` 是当前生产编排器。
- 抖音 Level 3 口播转写是当前最成熟的视频能力。
- `scripts/transcribe_douyin_once.ps1` 支持依赖 DryRun、真实转写、stdout transcript、stderr 日志。
- `tools/card_composer.py` 负责模型写卡。
- `tools/card_quality_gate.py` 负责正式卡质量门禁。
- `tools/write_siyuan.py` 负责当前 SiYuan 写入。
- `runtime/jobs` 保存可复盘任务记录。

## Experimental Capabilities

- Level 4 OCR：RapidOCR 本体可用，图片 OCR 和测试视频抽帧 OCR 已验证；真实抖音 OCR 仍依赖稳定 video_path/mp4 获取，不能承诺每条视频可达。
- Level 5 评论增强：已实验成功，但依赖登录态、页面状态、公开评论可见性、加载情况和平台限制，不能承诺批量稳定完整抓取。
- HTTP 长任务：当前可同步调用；后续应升级为 async job、SSE 或 WebSocket。
- Storage Sink：未来可接自建数据库，当前以 SiYuan writer 为生产写入点。

## Default Intake Flow

```text
Chat Gateway / 微信桥 / debug CLI
-> MessageEvent
-> Handler
-> run_link_job.py
-> Source Reader
-> Media Processing
-> Model Composer
-> Quality Gate
-> Taxonomy Router
-> Storage Sink
-> runtime/jobs
```

默认规则：

- 自动化入口默认自动处理和入库。
- 用户明确要求只预览、不保存、不下载或不转写时，必须尊重。
- 正式卡必须经过模型层和质量门禁。
- 模型失败或质量门禁失败时，只能输出 `temporary_review_card` 或临时复核卡。
- 内容不足时可以写临时卡，但必须明确材料不足和后续动作。
- 低置信度不应伪装成高置信度正式知识卡。
- 正式卡通过质量门禁后再运行分类路由；分类器优先读取 `runtime/taxonomy_tree.json` 或配置的知识树快照，不能让 composer 或 fallback 自由决定目录。
- 正式卡材料已获取且质量门禁通过时，应进入配置的 Storage Sink 写入；如果没有既有分类但主题明确，且 `taxonomy_auto_create_from_proposal=true`，应使用 `CategoryCreateProposalV1` 的路径作为写入目标，而不是仅因分类树缺项堆到 `Inbox / 待分类`。
- AI 根路径不是分类兜底。即使真实知识树快照里存在 `AI / ...` 路径，分类器也必须先确认材料有 AI / Agent / 模型 / 知识系统 / 工程化等域内证据；普通穿搭、服装搭配、防晒、妆容、护肤、旅行、美食等生活方式内容不能因为出现“内容”“视频”等泛词就写入 `AI / 内容生产`。当 `taxonomy_auto_create_from_proposal=true` 且主题明确时，应写入非 AI proposal 路径，例如 `生活方式 / 穿搭`、`生活方式 / 防晒`，而不是继续堆到 `Inbox / 待分类`。

## Douyin Level 3 Protocol

抖音 Level 3 口播转写是当前稳定核心路径。

核心脚本：

```text
scripts/transcribe_douyin_once.ps1
```

依赖：

- `C:\Users\pppppqr\tools\douyin-transcriber\dyt.exe`
- `ffmpeg`
- `C:\Users\pppppqr\tools\whisper.cpp\Release\whisper-cli.exe`
- `C:\Users\pppppqr\.cache\whisper.cpp\models\ggml-base.bin`

协议：

1. 先执行 DryRun 检查依赖。
2. DryRun 通过后执行真实转写。
3. transcript 输出到 stdout。
4. 日志输出到 stderr。
5. 临时音频、视频、wav、transcript 放 TEMP。
6. 脚本本身不直接写入 SiYuan。
7. `run_link_job.py` 统一编排后续模型写卡、质量门禁和写入。
8. transcript 是原始材料，不能直接成为正式知识卡正文。

失败环节必须明确归类，例如脚本不存在、dyt 不存在、whisper-cli 不存在、模型不存在、ffmpeg 不可用、链接解析失败、音频或视频获取失败、whisper 转写失败、transcript 为空、SiYuan 写入失败或其他明确错误。

## OCR Boundary

OCR 是条件触发增强能力，不是每条视频的强制默认步骤。

可触发 Level 4 的情况：

- 用户明确要求 OCR。
- 视频明显包含 PPT、代码、流程图、字幕条、屏幕文字或工具界面。
- 口播 transcript 缺失画面关键信息。
- `run_link_job.py` 通过内容特征或配置判断需要 OCR。
- 小红书、抖音、混合图文或视频类链接的 Source Reader 失败、平台正文为空或材料不足时，应尝试已有网页图片捕获/OCR；即使 `image_count` 为 0 或未知，也不能只因为缺少图片计数就放弃视觉兜底。

边界：

- RapidOCR 本体可用。
- 图片 OCR 和测试视频抽帧 OCR 已验证。
- 真实抖音视频 OCR 依赖稳定 video_path/mp4。
- 如果没有可用视频文件、网页图片或帧图，必须说明无法进入 Level 4 的原因。

### Browser Page Lifecycle For Visual Readers

使用 CDP 打开的页面读取、网页图片 OCR、评论抓取和通用渲染 fallback 必须默认阻止媒体播放：

- 启动本地浏览器时使用 `--autoplay-policy=user-gesture-required`；
- 页面导航前注入 media playback guard，覆盖媒体 `play()`、移除 `autoplay`、静音并暂停 `video/audio`；
- 不允许为了抓取视频源主动调用 `video.play()`；
- 复用平台授权浏览器时，只能新建临时 target / 标签页处理当前任务，采集结束后关闭这个临时 target，不关闭用户用于授权的主窗口；
- 自行启动的临时/headless 浏览器进程必须在脚本结束时关闭；
- `ocr_web_images.py` 结果应保留 `page_cleanup` 等可复盘字段。

## Comment Boundary

Level 5 评论增强是机会型增强能力。

进入条件：

- 页面可访问。
- 登录态可用。
- 公开评论区可见。
- 未遇到验证码、登录墙、App 拉起限制或加载失败。
- 用户没有明确要求不读评论。

规则：

- 只读取公开可见评论。
- 不绕过登录、验证码、付费墙、DRM 或平台限制。
- 不暴力刷新，不高频请求。
- 评论摘要必须绑定当前 job 的 `comments_hash`。
- 禁止复用其他 job 的评论、标题或互动信号。

评论增强失败时，说明具体原因，并保留 Level 3 或 Level 4 已有成果。

## Model Composer Rule

`tools/card_composer.py` 是正式卡生成的模型层。

规则：

- 只能基于当前 job 的 content、transcript、ocr、comments 和 input 材料写作。
- 必须输出结构化 card JSON 和 markdown。
- 必须保留 `comments_hash`。
- 不得编造作者、发布时间、指标、评论或视频观点。
- 不得把 transcript 大段复制到摘要、核心观点和知识块主体。
- 模型不可用时返回 `temporary_review_card`，不生成正式卡。

## Quality Gate Rule

`tools/card_quality_gate.py` 是正式卡质量门禁。

必须阻止：

- composer 未成功的卡。
- 模型信息为空的正式卡。
- 摘要过长或过像 transcript 的卡。
- 核心观点不足的卡。
- 知识块不足的卡。
- `comments_hash` 不匹配的卡。
- 有评论材料但未标 Level 5 的卡。
- 大段 transcript 出现在正文的卡。
- 标题仍是 URL slug 的卡。
- 串入相邻 job 标题或评论材料的卡。

未通过时降级为 `temporary_review_card` 或临时复核卡。

## Storage Rule

当前 Storage Sink 是 SiYuan writer，未来可扩展到自建数据库。

规则：

- 不直接修改 SiYuan 工作空间文件。
- 写入必须通过 `tools/write_siyuan.py`、SiYuan API 或未来 Storage Sink。
- 不覆盖、删除、移动、批量重命名已有笔记。
- 用户对已入库笔记不满意时，聊天入口应先澄清目标笔记、不满意点和期望改法，形成可复盘修改意图；不得让模型声称已经覆盖、删除、移动或直接修改旧笔记。
- 自动写入优先新建笔记。
- SiYuan writer 对正式卡优先消费 `taxonomy_decision.json`，写入 `<siyuan_taxonomy_root_path>/<recommended_path>/<title>`；非正式卡仍走 Inbox。
- 同一路径重复时按 `siyuan_duplicate_policy=skip_exact_path` 跳过，不覆盖旧笔记。
- 写入完成后报告标题、目录、路径、是否临时卡、job_id。
- 写入失败时保留本地 card 草稿和 job 结果。

## Temporary Files Rule

- 项目目录不保存音视频大文件。
- 项目目录不保存 OCR 帧图。
- 项目目录不保存长期临时 wav/mp3/mp4。
- 大文件、中间文件、帧图进入 TEMP。
- 转写临时目录使用 `$env:TEMP\LucasTranscribe`。
- OCR 临时目录使用 `$env:TEMP\LucasVideoOCR`。
- `runtime/jobs` 只保存结构化 JSON、日志摘要、card 结果和状态。
- `scripts/cleanup_lucas_temp.ps1` 只清理 TEMP 下 Lucas 相关临时目录。
- 清理脚本不得清理工具目录、模型文件、SiYuan 内容或项目源码。

## Issue Resolution Log Rule

入库链路中已经定位并修复的问题，必须归档到 `docs/ISSUE_RESOLUTION_LOG.md`。

记录内容应包含：

- 问题现象；
- 影响范围；
- 根因；
- 修复点；
- 验证命令和结果；
- 残余风险；
- 后续规则或建议。

`docs/HANDOFF.md` 只保留当前交接所需的短指针，不承载完整复盘。不要把完整问题复盘写到不相关文档、聊天记录或临时文件里。

## Knowledge Card Format

正式知识卡应包含：

- 来源信息；
- 读取等级；
- 材料来源；
- 一句话总结；
- 核心观点；
- 关键知识块；
- 方法论或流程；
- 可复用价值；
- 应用建议；
- 风险与不确定性；
- 后续动作；
- 标签。

视频知识卡不使用旧的项目启发栏目，统一使用“可复用价值”“应用建议”“后续动作”。

## Hard Prohibitions

- 不把 MCP 写成生产自动化依赖。
- 不把低置信度材料写成高置信度正式卡。
- 不把 transcript 原文大段塞进正文主体。
- 不把模型失败或质量门禁失败的结果标成正式卡。
- 不把 Level 4/5 说成所有视频都稳定可达。
- 不复用其他 job 的 comments_hash、评论样本、标题或互动信号。
- 不执行任意用户消息内容。
- 不用 `shell=True` 拼接用户输入。
- 不泄露 token、API key、cookie 或账号信息。
- 不直接读写 SiYuan 工作空间文件。
