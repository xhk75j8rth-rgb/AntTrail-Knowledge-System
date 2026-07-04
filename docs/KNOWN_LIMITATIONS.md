# Known Limitations

AntTrail is usable as a local-first source project, but it is still an early system. This page documents current limitations so users can understand what is expected, what may fail, and where contributions are welcome.

## Intake And Media

- Image-only sources may fail or return incomplete results when OCR cannot extract enough text.
- Video and social-platform intake depends on local tools and upstream website behavior, so it can break when a platform changes its page structure, rate limits, or access rules.
- OCR, transcription, and link extraction can be slower on large files, long videos, or machines without optimized local models.
- Some workflows need optional tools such as `ffmpeg`, `dyt`, `whisper-cli`, RapidOCR, or local model files. The base clone does not include those tools.

## AI Quality

- Card composition quality depends on the configured model provider, prompt fit, and source quality.
- Sparse or noisy source material can produce cards that require manual review.
- The quality gate may mark generated cards as pending review when confidence is low or required fields are incomplete.
- The current system can miss context, over-summarize details, or produce incomplete source coverage for complex topics.

## Retrieval And Vector Search

- The default clone uses mock embedding and mock vector-store behavior for a low-friction local setup.
- Real semantic retrieval requires optional embedding setup and local model files.
- Retrieval ranking and hybrid search quality are still being improved.
- Vector indexing can be slow on large libraries or underpowered machines.

## Storage And Database Targets

- The primary local database path is Lucas Database through `POST /api/cards/ingest`.
- SiYuan and custom storage sinks require extra configuration and valid tokens.
- Additional database backends are not yet first-class integrations.
- Automatic classification and taxonomy placement may be wrong when source material is ambiguous or too broad.

## Performance And Operations

- Some long-running intake jobs are slower than expected and may need better progress reporting.
- Error handling is improving, but some failures can still surface as generic warnings.
- The development workflow is source-based. A one-click installer or packaged `.exe` is not currently provided.
- Windows is the primary tested environment. Other operating systems may need path or script adjustments.

## Security And Privacy

- Do not expose the local services to the public internet without reviewing tokens, CORS, and local data paths.
- Do not commit generated local config, API keys, databases, attachments, logs, or model caches.
- Review generated cards before relying on them for important decisions.

