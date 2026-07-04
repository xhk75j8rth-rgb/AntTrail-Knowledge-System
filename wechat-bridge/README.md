# WeChat Bridge Staging

这个目录用于临时放置“开源微信桥”的接入说明、配置样例和转发示例。

它不是 `intake-control/` 或 `lucas-database/` 的运行根，也不直接写数据库、SiYuan 或项目内部文件。微信桥只负责登录微信、接收消息、把消息转给 Lucas Chat Gateway，并把 `reply_text` 发回微信。

## 推荐连接方式

主页面微信按钮使用 Lucas Chat Gateway 桥：

```text
微信扫码登录或复用本机登录态
-> 收到普通文本或链接
-> POST intake-control /api/chat/messages/async
-> MessageEvent(channel=wechat)
-> Chat Gateway router
-> 普通文本：fallback / RAG / 数据库问答
-> 链接：tools/run_link_job.py -> 模型写卡 / 质量门禁 / Storage Sink
-> reply_text 或批次最终回执
-> 微信桥回发微信
```

本机已找到并迁移的包见：

```text
wechat-bridge/vendor/cli-wechat-bridge/
```

正常启动用本仓库脚本，脚本会自动设置 Chat Gateway 地址：

```powershell
.\wechat-bridge\scripts\start-lucas-wechat-gateway-bridge.ps1
```

如果本机已有 `.cli-bridge/account.json` 登录态，普通启动会直接复用，不会重复显示二维码。需要新用户扫码或切换微信账号时：

```powershell
.\wechat-bridge\scripts\start-lucas-wechat-gateway-bridge.ps1 -ForceRelogin
```

主页面弹窗里的“重新扫码登录”按钮会走同一条强制扫码路径。

只测试 Lucas link entry 接线，不触发真实入库：

```powershell
.\wechat-bridge\scripts\test-link-entry.ps1 -Message "https://example.com" -Json
```

关键点：`CLI-WeChat-Bridge` 包里默认的 `LUCAS_LINK_PROJECT_ROOT` 指向旧目录 `Lucas-SiYuan-Codex`。在这个合并测试仓库里必须覆盖为：

```text
C:\Users\pppppqr\Desktop\Lucas-Knowledge-System-MergeTest\intake-control
```

旧 link entry 脚本仍可用于兼容测试，但主页面微信桥不再通过旧终端桥启动。

也可以继续使用通用 HTTP 转发方式：

```text
微信扫码登录的开源桥
-> 收到文本/链接消息
-> POST /api/chat/messages/async
-> Chat Gateway 转成 MessageEvent
-> link_handler / fallback_handler
-> tools/run_link_job.py
-> 模型写卡 / 质量门禁 / Storage Sink
-> 返回 reply_text 或 batch_id
-> 微信桥回复用户
```

生产路径里不要让微信桥直接调用 MCP、直接写 SiYuan 工作区、直接写 `lucas-database/data/lucas.db`，也不要绕过 `intake-control` 的质量门禁。

## 启动顺序

1. 启动 intake 服务。

   从仓库根目录可以使用已有 wrapper：

   ```powershell
   .\scripts\dev-intake.ps1
   ```

   或者进入子项目根目录后启动：

   ```powershell
   Set-Location .\intake-control
   python -m uvicorn server.chat_api:app --host 127.0.0.1 --port 3963
   ```

2. 确认服务可用。

   ```powershell
   Invoke-RestMethod http://127.0.0.1:3963/health
   Invoke-RestMethod http://127.0.0.1:3963/api/system/status
   ```

3. 在开源微信桥的消息回调里，把微信消息转成下面的 payload。

   ```json
   {
     "channel": "wechat",
     "conversation_id": "room-or-contact-id",
     "sender_id": "wechat-user-id",
     "sender_name": "微信昵称",
     "message_type": "text",
     "text": "https://example.com/some-link",
     "raw_payload": {},
     "metadata": {
       "adapter": "external-wechat-bridge",
       "bridge_project": "your-open-source-bridge-name"
     },
     "timeout_sec": 3600
   }
   ```

4. POST 到异步入口。

   ```powershell
   Invoke-RestMethod -Method Post `
     -Uri http://127.0.0.1:3963/api/chat/messages/async `
     -ContentType application/json `
     -Body '{"channel":"wechat","conversation_id":"wechat-bridge","sender_id":"lucas","message_type":"text","text":"https://example.com","timeout_sec":3600}'
   ```

5. 把返回的 `reply_text` 发回微信。

   如果返回 `data.async=true` 和 `data.batch_id`，说明任务已进入后台队列。微信桥可以先回 `reply_text`，再按需轮询：

   ```text
   GET http://127.0.0.1:3963/api/chat/batches/{batch_id}
   ```

## 开源微信桥应该接在哪里

把开源项目放在这个目录下的任意子目录即可，例如：

```text
wechat-bridge/
  vendor/
    cli-wechat-bridge/
  examples/
  README.md
```

如果这个开源项目支持 Node.js 消息回调，可以参考：

```text
wechat-bridge/examples/forward-to-chat-gateway.mjs
```

如果它只能调用命令行脚本，可以参考：

```text
wechat-bridge/examples/forward-to-legacy-cli.ps1
```

## Dry Run

调试阶段建议先 dry-run，确认微信消息能进 Chat Gateway，但不触发真实链接处理或写入。

HTTP 方式：

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:3963/api/chat/messages/async?dry_run=true" `
  -ContentType application/json `
  -Body '{"channel":"wechat","conversation_id":"wechat-bridge","sender_id":"lucas","message_type":"text","text":"https://example.com"}'
```

CLI 方式：

```powershell
Set-Location .\intake-control
python tools\wechat_link_entry.py --message "https://example.com" --extract-only --json
```

## 安全边界

- 不提交微信登录态、二维码图片、cookies、tokens、API keys。
- 不把微信桥做成 MCP 生产依赖。
- 不从微信消息里拼接 shell 命令。
- 不直接写 SiYuan 工作区文件。
- 不直接写 `lucas-database/data/lucas.db`。
- 正式卡必须继续经过模型写卡层和质量门禁。

## 当前需要你补充的信息

把你选定的开源微信桥 GitHub 地址或本地目录发过来后，需要对齐三件事：

- 它的消息回调函数名和消息字段。
- 它发送回复消息的 API。
- 它是否支持常驻进程、插件脚本、HTTP webhook，还是只能调用命令行。

对齐后，只需要把这个目录里的示例改成该项目的真实 adapter。
