# Lucas Retrieval MCP Server

Local stdio MCP server for Lucas Database Agent Retrieve API.

It exposes one MCP tool:

- `lucas_retrieve`: calls `POST /api/agent/retrieve` and returns grounded `context`, `sources`, `citations`, `answerability`, `confidence`, and `warnings`.

## Run

```powershell
node integrations\mcp-lucas-retrieval\server.mjs
```

The server reads newline-delimited JSON-RPC messages from stdio.

## Codex

```powershell
codex mcp add lucas-retrieval -- node C:\Users\pppppqr\Desktop\Lucas-Knowledge-System-MergeTest\lucas-database\integrations\mcp-lucas-retrieval\server.mjs
codex mcp get lucas-retrieval
```

For non-interactive `codex exec` experiments, set approval for this local server in `C:\Users\pppppqr\.codex\config.toml`:

```toml
[mcp_servers.lucas-retrieval]
command = "node"
args = ['C:\Users\pppppqr\Desktop\Lucas-Knowledge-System-MergeTest\lucas-database\integrations\mcp-lucas-retrieval\server.mjs']
startup_timeout_sec = 20.0
tool_timeout_sec = 120.0
default_tools_approval_mode = "approve"
```

Restart or start a new Codex session after adding the server. Existing sessions do not automatically gain newly registered MCP tools.

## Verified Codex Smoke

Positive query:

```text
status: ready
can_answer: true
first_source_title: AI 生成 iPhone App 后如何准备上架 App Store
```

Negative query:

```text
status: low_confidence
can_answer: false
warnings: low_confidence, empty_context
```

## Verify Codex Actually Used MCP

In `codex exec` output, look for:

```text
mcp: lucas-retrieval/lucas_retrieve started
mcp: lucas-retrieval/lucas_retrieve (completed)
```

If those lines are missing, Codex did not use the Lucas MCP tool.

For reliable tests, explicitly ask Codex to use `lucas_retrieve` or to search Lucas Database through MCP. Do not rely on hidden project-level agent instructions.

## Configuration

Defaults work with the local Lucas API at `http://127.0.0.1:8765`.

Optional environment:

- `LUCAS_AGENT_RETRIEVE_BASE_URL`
- `LUCAS_AGENT_RETRIEVE_TOKEN`
- `LUCAS_AGENT_RETRIEVE_TIMEOUT_MS`

For real retrieval quality, start Lucas Database with BGE-M3 and sqlite-vec enabled.
