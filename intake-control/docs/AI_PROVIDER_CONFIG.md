# AI Provider Config

## Purpose

AI Layer 不应该只绑定 DeepSeek。当前实现把模型供应商配置收敛到 `ai_layer/provider_config.py`，由本地 UI 和 FastAPI 配置接口管理，后续可逐步接入 OpenAI、Claude、Gemini、国内模型平台和 OpenAI-compatible 中转站。

## Local Config

本地配置文件：

```text
config/ai_layer.local.json
```

该文件已加入 `.gitignore`，可以保存本机 API key。后端接口不会回传完整 key，只返回：

- `api_key_present`
- `api_key_last4`
- `api_key_env`

也可以继续使用环境变量，例如：

- `DEEPSEEK_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`
- `DASHSCOPE_API_KEY`
- `MOONSHOT_API_KEY`
- `SILICONFLOW_API_KEY`
- `OPENROUTER_API_KEY`
- `AI_LAYER_API_KEY`

## UI Config

打开：

```text
http://127.0.0.1:3963/ui
```

在“模型配置”区域选择提供商后，页面会自动填入默认 `Base URL` 和模型名。你可以直接改成自己的中转站地址，然后保存。

保存接口：

```http
POST /api/ai/config
```

示例：

```json
{
  "provider_id": "openai_compatible",
  "base_url": "https://relay.example.com/v1",
  "model": "deepseek-chat",
  "api_key": "sk-..."
}
```

读取接口：

```http
GET /api/ai/config
GET /api/ai/providers
POST /api/ai/models
```

`POST /api/ai/models` 会用当前 `Base URL` 和 API key 请求供应商的模型列表。OpenAI-compatible 中转站通常应支持：

```text
GET <Base URL>/models
```

前端的“获取模型列表”按钮会调用这个接口，并把返回模型填入模型名输入框的可选列表。该动作不保存配置、不触发入库、不写 SiYuan。

## Provider Presets

当前内置：

- DeepSeek：`https://api.deepseek.com`
- OpenAI：`https://api.openai.com/v1`
- Gemini OpenAI 兼容：`https://generativelanguage.googleapis.com/v1beta/openai`
- Anthropic Claude：`https://api.anthropic.com/v1`
- DashScope 兼容：`https://dashscope.aliyuncs.com/compatible-mode/v1`
- Moonshot Kimi：`https://api.moonshot.cn/v1`
- SiliconFlow：`https://api.siliconflow.cn/v1`
- OpenRouter：`https://openrouter.ai/api/v1`
- 自定义 OpenAI 兼容中转站：默认 `http://127.0.0.1:8000/v1`

## DeepSeek And Vision Boundary

当前写卡链路是文本 composer：它把页面读取、转写、OCR、评论等结构化材料交给模型生成知识卡。

DeepSeek 当前适合作为文本写卡模型。图片识别、视频画面理解、截图理解不应默认假设由 DeepSeek 完成。视觉能力后续应拆成独立 Source Reader / Media Processing 步骤，并选择明确支持 vision 的 provider，例如 OpenAI、Gemini、Claude 或支持多模态模型的中转站。

## Runtime Behavior

`ai_layer/model_router.py` 读取当前激活 provider：

1. 测试可用 `AI_LAYER_PROVIDER=mock`。
2. 日常使用优先读取 `config/ai_layer.local.json`。
3. 没有本地配置时默认 DeepSeek-compatible。
4. API key 可以来自本地配置或环境变量。

`card_composer.py` 不直接知道具体供应商，只调用 AI Layer。

## Security

- UI 不保存 key 到浏览器本地存储。
- FastAPI 不返回完整 key。
- job/result 不应该记录完整 key。
- AI Layer 不执行任意命令。
- AI Layer 不直接访问 SiYuan。
- AI Layer 不调用 MCP。
- 外部动作仍由固定 handler 和主链路执行。

## Next

- 为视觉模型增加 `generate_multimodal()` 或 `analyze_image()` 接口。
- 把 OCR/截图/视频帧结果与 vision provider 解耦。
- 给长任务增加异步 job 和模型调用重试策略。
- 支持 per-task provider，例如写卡用 DeepSeek，图片理解用 Gemini/OpenAI。
