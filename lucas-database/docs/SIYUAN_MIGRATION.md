# SIYUAN_MIGRATION.md / 从 SiYuan 迁移到 Lucas Database

Lucas Database 不把 SiYuan Markdown 当作结构化主库。迁移时建议把 SiYuan 当作来源：正文进入 Node 内容视图，图片、视频、PDF、压缩包等进入节点附件；后续如果要生成结构化卡片，再通过 `POST /api/cards/ingest` 写入 `ComposedCardV1`。

## 当前推荐路径

1. 在 SiYuan 里导出 Markdown。
2. 确认导出目录里包含 Markdown 文件和对应的 `assets` 资源文件。
3. 在 Lucas Database 中按原来的笔记本/文档层级创建 Node。
4. 用 `POST /api/write-by-path` 把 Markdown 正文写入对应 Node。
5. 用 `POST /api/nodes/:id/attachments` 把图片、视频和本地文件挂到同一个 Node。
6. 检查前端附件区：图片会直接预览，视频会显示播放器，其他文件显示文件名和大小。

也可以走 SiYuan Kernel API：SiYuan 的官方 API 文档包含 `/api/export/exportMdContent` 和 `/api/export/exportResources`，本地服务通常运行在 `http://127.0.0.1:6806`，调用时使用 `Authorization: Token <SIYUAN_API_TOKEN>`。这条路线适合以后做一键迁移器。

## 迁移前需要准备什么

- 一份 SiYuan 导出的 Markdown 目录。
- 一份导出的资源目录，通常包含图片、视频和其他附件。
- Lucas Database API Token。设置页可以查看、验证或生成本地 API Key。
- 可选：一份路径映射表，用来决定 SiYuan 文档路径对应 Lucas 的哪个 Node 路径。

## 手动导入正文

可以先用 `write-by-path` 把一篇文档写入 Lucas：

```bash
curl -X POST "http://localhost:8765/api/write-by-path" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -d '{
    "path": "/SiYuan 导入/笔记本A/文档标题",
    "content": "# 文档标题\n\n这里是从 SiYuan 导出的 Markdown 正文。",
    "mode": "overwrite",
    "actor": "migration"
  }'
```

## 手动导入附件

先通过 `GET /api/tree` 或 `GET /api/search` 找到目标节点 ID，然后上传文件：

```bash
curl -X POST "http://localhost:8765/api/nodes/node_xxx/attachments" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -F "file=@/path/to/assets/image.png" \
  -F "actor=migration"
```

同一个 SiYuan 文档引用到的图片、视频或本地文件都可以作为附件挂到同一个 Lucas 节点。当前 V0 不会自动改写 Markdown 里的资源链接；前端会在节点内容上方展示附件区。

## 后续一键迁移器的设计方向

后续可以新增一个本地迁移脚本：

- 扫描 SiYuan 导出的 Markdown 文件。
- 根据目录结构生成 Lucas Node 路径。
- 调用 `POST /api/write-by-path` 写入正文。
- 解析 Markdown 里的 `assets/...` 引用。
- 调用附件上传接口，把被引用资源挂到对应 Node。
- 生成迁移报告，列出成功、缺失资源和未识别链接。

这个方向不会改变 Lucas 的主数据原则：结构化知识仍进入 `cards.raw_json`，Markdown 只作为来源正文或展示视图。

## 参考

- SiYuan API 文档：`https://github.com/siyuan-note/siyuan/blob/master/API.md`
