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

## Media, OCR, And Transcription

These tools are only needed for advanced media intake. They are not required to start the database app or the basic intake UI.

### Video And Douyin Transcription

Douyin and video transcription may use:

```text
ffmpeg
ffprobe
dyt
whisper-cli
Whisper model file, for example ggml-base.bin
```

Suggested environment variables:

```text
LUCAS_DYT_EXE=C:\path\to\dyt.exe
LUCAS_WHISPER_CLI=C:\path\to\whisper-cli.exe
LUCAS_WHISPER_MODEL_PATH=C:\path\to\ggml-base.bin
```

`ffmpeg` and `ffprobe` should be available on `PATH`.

If these are missing, video transcription can fail, return no speech, or fall back to weaker source material. The rest of the local app can still run.

### OCR

OCR uses the script:

```text
intake-control/tools/ocr_media.py
```

It expects a Python environment where RapidOCR can be imported. By default it uses the current Python executable, but you can point it at a dedicated environment:

```text
LUCAS_RAPIDOCR_PYTHON=C:\path\to\rapidocr-venv\Scripts\python.exe
```

`ffmpeg` and `ffprobe` are also needed when OCR samples frames from a video.

If OCR is missing, image-only material may be incomplete and generated cards may be lower confidence or held for review.

## Retrieval And Embeddings

The default clone runs with mock embedding/vector behavior so users can start the app without downloading large models.

Real BGE-M3 retrieval needs an optional Python environment and model files. Common variables include:

```text
LUCAS_EMBEDDING_PROVIDER=bge-m3
LUCAS_VECTOR_STORE=sqlite-vec
BGE_M3_MODEL_DIR=C:\path\to\BAAI\bge-m3
LUCAS_BGE_M3_PYTHON=C:\path\to\.venv-bge-m3\Scripts\python.exe
```

If these are missing, the app should still run, but semantic retrieval quality will be limited to the default mock or fallback behavior.

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
