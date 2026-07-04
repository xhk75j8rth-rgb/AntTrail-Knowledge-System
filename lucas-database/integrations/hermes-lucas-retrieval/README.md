# Lucas Retrieval for Hermes

Hermes user plugin for Lucas Database Agent Retrieve API.

It exposes one tool:

- `lucas_retrieve`: calls `POST /api/agent/retrieve` and returns grounded `context`, `sources`, `citations`, `answerability`, `confidence`, and `warnings`.

## Install

Copy this folder to the Hermes user plugin root:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.hermes\plugins\lucas_retrieval"
Copy-Item -Recurse -Force ".\integrations\hermes-lucas-retrieval\*" "$env:USERPROFILE\.hermes\plugins\lucas_retrieval\"
hermes plugins enable lucas_retrieval
```

Debug discovery:

```powershell
$env:HERMES_PLUGINS_DEBUG='1'
hermes plugins list
```

## Configuration

Defaults work with the local Lucas API at `http://127.0.0.1:8765`.

Optional environment:

- `LUCAS_AGENT_RETRIEVE_BASE_URL`
- `LUCAS_AGENT_RETRIEVE_TOKEN`

For real retrieval quality, start Lucas Database with BGE-M3 and sqlite-vec enabled.
