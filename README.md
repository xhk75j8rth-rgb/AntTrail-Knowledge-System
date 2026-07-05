# AntTrail Knowledge System

AntTrail 是一个本地优先的个人知识系统。这个仓库以源码形式发布：用户 clone 仓库后，在本机安装依赖并运行。

仓库不会包含你的本地数据库、附件、API Key、模型缓存、任务历史、登录态、`node_modules` 或其他本机运行数据。

项目包含两个可运行部分：

- `lucas-database/`：Electron + React + Express + SQLite 的本地知识数据库。
- `intake-control/`：FastAPI intake 网关，用于链接、聊天消息、OCR、转写流程和存储写入。

稳定集成边界是：

```text
ComposedCardV1 -> POST /api/cards/ingest
```

## 快速开始

### 环境要求

- Windows 10/11
- Node.js `22.5.0` 或更新版本
- Python `3.10` 或更新版本
- Git

### 安装依赖

```powershell
.\scripts\setup-dev.ps1
```

这个脚本会安装 Node 依赖，创建 `intake-control/.venv`，安装 Python 依赖，并在缺少本地配置时从安全示例文件生成配置。

开发模式下，它还会在 `intake-control/.env` 中创建 `LUCAS_DB_API_KEY=lucas-local-dev-token`。这个值匹配全新本地数据库的默认开发 token。共享机器或暴露服务前，请通过数据库设置或环境变量更换 token。

### 启动数据库

```powershell
.\scripts\dev-database.ps1
```

打开：

```text
http://127.0.0.1:5173
```

### 启动 Intake UI/API

另开一个 PowerShell：

```powershell
.\scripts\dev-intake.ps1
```

打开：

```text
http://127.0.0.1:3963/ui
```

也可以用隐藏 PowerShell 窗口同时启动两部分：

```powershell
.\scripts\dev-all.ps1
```

## Clone 后默认可用的能力

- AntTrail Database 可以在本机启动。
- SQLite 数据库会自动创建在 `lucas-database/data/`。
- 数据库浏览器 UI 可以创建、编辑、搜索、上传附件，并查看图谱数据。
- 检索/索引接口默认使用 mock embedding provider 和 mock vector store，方便先跑起来。
- Intake UI/API 可以启动，并显示配置和状态页面。
- 基础聊天和 dry-run 链接流程可以在没有私有 key 的情况下运行。

## 项目状态

AntTrail 目前是一个早期的本地优先源码项目。核心路径已经可以运行，但部分高级流程仍依赖可选本地工具、API Key、模型文件和后续质量优化。

- 已知限制：`docs/KNOWN_LIMITATIONS.md`
- 路线图：`docs/ROADMAP.md`
- 可选依赖：`docs/OPTIONAL_DEPENDENCIES.md`

## 可选功能

下面这些能力需要额外配置本地工具、模型文件或 API Key：

- AI 写卡：需要配置模型提供商和 API Key。
- SiYuan 写入：需要 SiYuan endpoint 和 token。
- 抖音/视频转写：需要 `ffmpeg`、`dyt`、`whisper-cli` 和 Whisper 模型文件。
- OCR：需要 RapidOCR；视频抽帧还需要 `ffmpeg`/`ffprobe`。
- 真实 BGE-M3 检索：需要可选的 `.venv-bge-m3` 环境和 BGE-M3 模型文件。
- 微信桥：需要兼容的第三方桥包，或你自己的桥接 adapter。GitHub 源码版不会包含 `wechat-bridge/vendor/` 下复制来的第三方包。
- 平台网页授权：需要用户在本机授权浏览器中自行登录。仓库不会包含你的 cookies、登录态或浏览器 profile。

没有这些可选依赖时，核心数据库和本地 UI 仍然可以运行。具体环境变量和安装说明见 `docs/OPTIONAL_DEPENDENCIES.md`。

## 本地配置

仓库会跟踪安全示例文件：

```text
intake-control/config/ai_layer.example.json
intake-control/config/storage.example.json
intake-control/config/link_pipeline.example.json
intake-control/config/taxonomy_tree.example.json
```

机器本地文件由 `setup-dev.ps1` 或 UI 生成，并被 git 忽略：

```text
intake-control/.env
intake-control/config/ai_layer.local.json
intake-control/config/storage.local.json
intake-control/config/link_pipeline.json
```

不要提交真实 API Key、token、本地数据库、附件、登录态、任务输出或模型缓存。

## 常用命令

只启动数据库：

```powershell
Push-Location .\lucas-database
npm install
npm run dev
Pop-Location
```

只启动 Intake：

```powershell
Push-Location .\intake-control
.\.venv\Scripts\python.exe -m uvicorn server.chat_api:app --host 127.0.0.1 --port 3963
Pop-Location
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
Invoke-RestMethod http://127.0.0.1:3963/health
```

检查仓库是否适合发布到 GitHub：

```powershell
.\scripts\check-github-ready.ps1
```

## Git 发布注意事项

这个仓库刻意不跟踪本地运行数据，包括：

- `node_modules/`
- Python 虚拟环境
- `.env`
- `*.local.json`
- SQLite 数据库
- 附件
- 日志
- runtime job 输出
- agent-only memory/skills
- `wechat-bridge/vendor/` 下复制来的第三方运行包
- cookies、网页登录态、二维码、账号数据

上传到 GitHub 前请运行：

```powershell
.\scripts\check-github-ready.ps1
```

正常提交流程：

```powershell
git add .
git status
git commit -m "Your commit message"
git push
```

`git status` 中不应该出现数据库、日志、`node_modules`、虚拟环境、本地配置、runtime job 输出、登录态或 agent memory 文件。
