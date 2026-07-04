# Roadmap

This roadmap describes likely development directions. It is not a guarantee of delivery order. The project will prioritize reliability, local-first usability, and clear data ownership.

## Near Term

- Improve clean first-run behavior for new users.
- Strengthen setup scripts and health checks.
- Add clearer error messages for missing tools, API keys, storage tokens, and model providers.
- Improve image-only intake and OCR fallback behavior.
- Improve queue progress reporting for long-running link, OCR, and transcription jobs.
- Add more tests around intake, card composition, quality gates, and storage writes.

## Retrieval And Knowledge Quality

- Improve hybrid retrieval with better keyword, vector, and reranking behavior.
- Make real embedding setup easier to enable after clone.
- Add better index status visibility in the UI.
- Improve retrieval confidence scoring and low-confidence explanations.
- Reduce incorrect taxonomy placement and improve review tools for reclassification.

## Card Review And Storage

- Make pending-review cards easier to inspect, edit, approve, or reject.
- Improve quality-gate diagnostics so users know why a card was held.
- Add safer retry and repair flows for failed storage writes.
- Support more storage targets and database backends as first-class options.
- Improve export and backup workflows.

## Media And Platform Intake

- Improve handling for pure images, screenshots, PDFs, videos, and social posts.
- Add more robust fallback paths when platform extraction fails.
- Support richer source evidence, including OCR snippets, transcript segments, and captured media metadata.
- Improve duplicate detection and source merging.

## Packaging And Distribution

- Keep the source-based GitHub workflow reliable first.
- Explore a packaged desktop build after the source setup is stable.
- Add one-click startup helpers where they reduce friction without hiding important configuration.
- Document release builds, upgrade paths, and local data migration before distributing packaged binaries.

## UI And Workflow

- Improve onboarding for first-time users.
- Add clearer settings screens for AI providers, storage targets, and retrieval settings.
- Improve task queue visibility and recoverability.
- Add better review flows for generated cards, taxonomy edits, and database ingestion results.

