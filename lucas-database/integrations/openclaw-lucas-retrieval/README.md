# Lucas Retrieval for OpenClaw

OpenClaw tool plugin for Lucas Database Agent Retrieve API.

It exposes one tool:

- `lucas_retrieve`: calls `POST /api/agent/retrieve` and returns grounded `context`, `sources`, `citations`, `answerability`, `confidence`, and `warnings`.

## Build and Validate

```powershell
cd integrations\openclaw-lucas-retrieval
npm install
npm run plugin:build
npm run plugin:validate
```

## Install Locally

```powershell
openclaw plugins install . --force
openclaw plugins enable lucas-retrieval
openclaw plugins inspect lucas-retrieval --runtime
```

Restart the OpenClaw Gateway after installing or updating the plugin.

## Configuration

Defaults work with the local Lucas API at `http://127.0.0.1:8765`.

Optional config/environment:

- `apiBaseUrl` or `LUCAS_AGENT_RETRIEVE_BASE_URL`
- `apiToken` or `LUCAS_AGENT_RETRIEVE_TOKEN`
- `defaultLimit`
- `defaultTokenBudget`
- `defaultMaxChunksPerSource`
- `timeoutMs`

For real retrieval quality, start Lucas Database with BGE-M3 and sqlite-vec enabled.
