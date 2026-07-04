# Optional Dependencies

The base repository is designed to clone, install, and run without private data or copied third-party runtime packages. Some advanced features require extra tools, accounts, API keys, or local model files.

## Quick Reference

| Feature | Required extra setup | Included in GitHub source? | What happens if missing |
|---|---|---:|---|
| AI card composition | Model provider API key and config | No | Chat/dry-run can work, but real card quality depends on the configured provider |
| SiYuan writing | SiYuan endpoint and token | No | Writes to SiYuan fail or are skipped |
| WeChat bridge | Compatible WeChat bridge package under `wechat-bridge/vendor/cli-wechat-bridge/` or an external adapter | No | WeChat bridge startup fails with a missing optional dependency message |
| Douyin/video transcription | `ffmpeg`, `dyt`, `whisper-cli`, Whisper model | No | Video transcription flows fail or return incomplete material |
| OCR | Local RapidOCR-compatible setup | No | Image-only intake may fail or produce low-confidence cards |
| Real semantic retrieval | Embedding environment, model files, vector-store setup | No | The default clone uses mock embedding/vector behavior |
| BGE-M3 retrieval | `.venv-bge-m3` and model files | No | BGE-M3 provider cannot run |

## WeChat Bridge

The GitHub source release intentionally excludes copied third-party runtime packages under:

```text
wechat-bridge/vendor/
```

The Lucas WeChat Gateway bridge currently expects a compatible `cli-wechat-bridge` package at:

```text
wechat-bridge/vendor/cli-wechat-bridge/
```

Required files include:

```text
wechat-bridge/vendor/cli-wechat-bridge/dist/wechat/setup.js
wechat-bridge/vendor/cli-wechat-bridge/dist/wechat/wechat-transport.js
```

If those files are missing, the WeChat bridge cannot start. This is expected for a clean source clone until the optional third-party bridge package is installed or copied locally.

You can also avoid the vendored package path by wiring your own external WeChat bridge to:

```text
POST http://127.0.0.1:3963/api/chat/messages/async
```

See `wechat-bridge/README.md` for the expected payload shape.

## Why These Are Not Committed

Do not commit these generated or third-party runtime files:

- API keys, tokens, cookies, login sessions, QR codes, or account data.
- Local databases, attachments, logs, job output, and model caches.
- `node_modules/`, Python virtual environments, and downloaded model files.
- Copied third-party runtime packages under `wechat-bridge/vendor/`.

This keeps the public repository clean, smaller, and safer for users to clone.

