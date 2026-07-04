# Report-Only Merge Plan: Intake Control + Lucas Database

## 1. 目标

把当前两个项目收拢成一个大项目视角，但第一阶段只做外壳式合并：

- 信息收集项目继续负责聊天入口、链接读取、转写、OCR、评论增强、模型写卡、质量门禁和 job 记录。
- Lucas Database / 求索数据库继续负责结构化主库、Node/Card/Graph、附件、向量索引、检索 API 和桌面 UI。
- 两者通过 `ComposedCardV1` 和 `POST /api/cards/ingest` 对接。

第一阶段不打散任何内部目录，不改生产主链路，不移动数据文件，不运行真实 intake，不写入 SiYuan 或 Lucas Database。

## 2. 推荐目录结构

建议新建一个大项目外壳目录，例如：

```text
C:\Users\pppppqr\Desktop\Lucas-Knowledge-System\
  intake-control\
  lucas-database\
  docs\
  scripts\
  AGENTS.md
  README.md
```

目录含义：

```text
intake-control\   当前 C:\Users\pppppqr\Desktop\Lucas-Knowledge-DB-Lab
lucas-database\   当前 C:\Users\pppppqr\Desktop\求索数据库
docs\             大项目级别总览、架构边界、操作索引
scripts\          大项目级别启动脚本，只负责 cd 到对应子项目后启动
AGENTS.md         大项目级 agent 路由说明
README.md         大项目入口说明
```

关键原则：不要把两个项目的 `docs`、`tools`、`backend`、`src`、`tests` 混到同一层。它们可以重名，因为它们属于不同子项目。

## 3. 合并级别

### Level 0: Report-only

当前阶段。只产出规划文档，不搬文件。

### Level 1: 外壳式合并

把两个项目作为 sibling 子目录放进同一个大目录。内部结构保持不变。

推荐先做这个级别。它本质上只是目录管理和索引调整，风险最低。

### Level 2: 统一启动和索引

在大项目根目录补：

- `README.md`
- `AGENTS.md`
- `docs/PROJECT_INDEX.md`
- `docs/COMMANDS.md`
- `scripts/dev-intake.ps1`
- `scripts/dev-database.ps1`
- `scripts/dev-all.ps1`

这些脚本只负责切换工作目录后调用原项目命令，不改原项目代码。

### Level 3: 契约和配置统一

统一两个项目之间的配置命名和运行地址，重点是：

- Lucas Database API 默认端口：当前数据库项目文档主线是 `8765`。
- 信息收集侧旧配置曾出现 `8768`，本机后续已改到 `8765`，合并文档里应固定为真实运行端口。
- 数据库项目写入 token 文档使用 `LUCAS_DB_API_TOKEN`。
- 信息收集侧 sink 使用 `LUCAS_DB_API_KEY` 读取本地存储配置。后续应明确二者是否保留兼容别名，或统一到一个环境变量。

### Level 4: 主链路可选 sink

等 Level 1-3 稳定后，再考虑改受保护主编排文件，让 `tools/run_link_job.py` 支持显式 storage target，例如：

```powershell
python tools\run_link_job.py --url <url> --sink siyuan,lucas_database
```

这一步需要单独任务、测试和回滚点。不要在目录合并阶段顺手做。

## 4. 不应复制或不应进入大项目源码管理的内容

从数据库项目迁移时，以下内容不要作为源码合并目标：

```text
node_modules\
dist\
dist-electron\
.venv-bge-m3\
*.pid
*.log
data\lucas.db
data\attachments\
```

从信息收集项目迁移时，以下内容也不要作为源码合并目标：

```text
.env
runtime\jobs\ 大量历史任务产物
__pycache__\
.browser-profile\
.playwright-mcp\
douyin-ocr-test.png
```

如需迁移真实数据库文件、附件或历史 job，应作为单独数据迁移任务处理，先备份，再复制，不和源码合并混在一起。

## 5. 大项目根 AGENTS.md 建议内容

大项目根 `AGENTS.md` 应只做路由，不替代两个子项目自己的规则。

建议写入以下规则：

```text
- 链接入库、Chat Gateway、微信桥、debug CLI、页面读取、转写、OCR、评论、模型写卡、质量门禁、SiYuan writer、job runtime：进入 intake-control。
- Node/Card/Graph、SQLite、附件、向量索引、检索 API、Electron/React UI：进入 lucas-database。
- 两项目生产对接边界是 ComposedCardV1 + POST /api/cards/ingest。
- 不要从大项目根目录直接运行子项目脚本，除非脚本明确 cd 到对应子项目。
- 不要把 MCP 写成生产依赖。
- 不要直接读写 SiYuan 工作空间文件。
- 不要直接修改 lucas-database\data\lucas.db，数据库写入走 API。
```

## 6. 合并后命令策略

大项目根启动脚本应使用显式 `Push-Location` / `Pop-Location`，避免相对路径错乱。

示例：

```powershell
# scripts/dev-intake.ps1
Push-Location "$PSScriptRoot\..\intake-control"
python -m uvicorn server.chat_api:app --host 127.0.0.1 --port 3963
Pop-Location
```

```powershell
# scripts/dev-database.ps1
Push-Location "$PSScriptRoot\..\lucas-database"
npm run dev
Pop-Location
```

```powershell
# scripts/dev-all.ps1
Start-Process powershell -ArgumentList "-NoExit", "-File", "$PSScriptRoot\dev-intake.ps1"
Start-Process powershell -ArgumentList "-NoExit", "-File", "$PSScriptRoot\dev-database.ps1"
```

这些脚本只是建议，真正创建脚本时应按当时端口、环境变量和运行方式再确认。

## 7. 需要更新的索引

外壳式合并后，主要改索引，不改业务逻辑。

大项目根：

- `README.md`：说明两个子项目职责和常用命令。
- `AGENTS.md`：说明 agent 应进入哪个子项目。
- `docs/PROJECT_INDEX.md`：列出关键文档和入口文件。
- `docs/COMMANDS.md`：列出启动、健康检查、dry-run、数据库写入 dry-run。
- `docs/STORAGE_BOUNDARY.md`：说明 SiYuan、Lucas Database、runtime/jobs 的边界。

信息收集项目内：

- 可在 README 或 handoff 中补一句：如果位于大项目结构下，项目根是 `intake-control`。
- 不应在第一阶段批量重写源码里的 `PROJECT_ROOT`。

数据库项目内：

- 可在 README 或 handoff 中补一句：如果位于大项目结构下，项目根是 `lucas-database`。
- 不应在第一阶段批量重写源码里的数据目录、附件目录或 Electron 配置。

## 8. 合并验收清单

外壳式合并完成后，只做静态和 dry-run 验证：

```powershell
# 大项目根
Get-ChildItem

# intake-control
Push-Location .\intake-control
python -B -m py_compile storage_config.py tools\write_lucas_database.py
python tools\write_lucas_database.py --help
Pop-Location

# lucas-database
Push-Location .\lucas-database
npm run typecheck
Pop-Location
```

可选只读健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:3963/health
Invoke-RestMethod http://127.0.0.1:8765/api/health
```

不要在目录合并验收阶段运行真实链接、真实抖音、真实模型写卡、OCR、评论抓取、SiYuan 写入或 Lucas Database 写入。

## 9. 风险

主要风险不是文件放在一起，而是后续命令从错误工作目录运行。

高风险点：

- `runtime/jobs` 被误认为大项目根下的统一目录。
- `.env` 被从一个子项目误读到另一个子项目。
- 数据库 `data/lucas.db` 被当源码移动或删除。
- `node_modules`、`dist`、临时 profile 或历史 job 被一起迁移，导致体积膨胀。
- agent 看到大项目根后绕过子项目 `AGENTS.md`。
- 信息收集侧和数据库侧 token 名称、端口说明不一致。

规避方式：

- 子项目保留自己的根目录和 `AGENTS.md` / memory 文档。
- 大项目根只做路由。
- 所有启动脚本先 `cd` 到子项目。
- 数据迁移和源码合并分开做。
- 第一阶段只做 report-only 和外壳式合并，不改生产主链路。

## 10. 建议结论

Lucas 的判断是对的：这两个项目本质上可以先作为两个子项目放进一个大目录，短期不会有大问题。

最推荐的第一步是：

```text
新建 Lucas-Knowledge-System
复制/移动两个项目为 intake-control 和 lucas-database
补根 README / AGENTS / PROJECT_INDEX / COMMANDS
不改核心代码
不迁移真实数据
不运行真实任务
```

等这个外壳稳定后，再单独处理配置统一、启动脚本、数据库 sink 默认化和最终主链路整合。
