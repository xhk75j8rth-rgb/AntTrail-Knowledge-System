# Merge Test Report

Date: 2026-07-02

## Result

The non-destructive merge test succeeded.

Test root:

```text
C:\Users\pppppqr\Desktop\Lucas-Knowledge-System-MergeTest
```

Subprojects:

```text
intake-control\
lucas-database\
```

## What Was Done

- Created a new merge-test shell directory.
- Copied the current intake project into `intake-control/`.
- Copied Lucas Database into `lucas-database/`.
- Added root-level `README.md`, `AGENTS.md`, `docs/PROJECT_INDEX.md`, `docs/COMMANDS.md`, and wrapper scripts.
- Excluded or removed machine-local/runtime data from the source copy.
- Installed database dependencies inside the test copy only so `npm run typecheck` could run.

## Excluded From Source Copy

Intake side:

```text
.git
.env
__pycache__
.browser-profile
.playwright-mcp
runtime\jobs
douyin-ocr-test.png
```

Database side:

```text
.git
node_modules
dist
dist-electron
.venv-bge-m3
data
*.pid
*.log
*.tsbuildinfo
```

Note: `lucas-database\node_modules` exists after validation because `npm install --ignore-scripts --no-audit --no-fund` was run inside the merge-test copy. It was not copied from the original database project.

## Validation Commands

From `intake-control/`:

```powershell
python -B -m py_compile storage_config.py tools\write_lucas_database.py server\chat_api.py
python tools\write_lucas_database.py --help
python -B -m unittest tests.test_lucas_database_sink -v
```

Result:

```text
py_compile: passed
write_lucas_database.py --help: passed
tests.test_lucas_database_sink: 10 tests OK
```

From merge-test root:

```powershell
PowerShell AST parse for scripts\dev-intake.ps1
PowerShell AST parse for scripts\dev-database.ps1
PowerShell AST parse for scripts\dev-all.ps1
```

Result:

```text
all wrapper scripts parse OK
```

From `lucas-database/`:

```powershell
npm install --ignore-scripts --no-audit --no-fund
npm run typecheck
```

Result:

```text
npm install: added 306 packages
npm run typecheck: passed
```

## Not Performed

- Did not move the original projects.
- Did not start real Chat Gateway or Lucas Database services.
- Did not run real link intake.
- Did not run real Douyin processing.
- Did not call OCR, comment fetching, or model composition.
- Did not write to SiYuan.
- Did not write to Lucas Database.
- Did not call MCP.

## Conclusion

The two projects can be merged safely as sibling subprojects under a shell root.

The main operational rule remains:

```text
Run commands from the correct subproject root, or through wrapper scripts that cd first.
```

The next step, if this layout becomes the real project layout, is to create the final non-test root and move/copy source directories using the same exclusions. Data migration should remain separate.

## Real Integration Smoke

Date: 2026-07-02

This follow-up test started real services from the merge-test layout on isolated ports:

```text
Lucas Database API: http://127.0.0.1:18765
Intake API:         http://127.0.0.1:13963
```

Test database:

```text
C:\Users\pppppqr\Desktop\Lucas-Knowledge-System-MergeTest\runtime-test\lucas-merge-test.db
```

### Checks

- `GET /api/health` on Lucas Database returned `ok=true`, `auth.source=env`, `embedding_provider=mock`, `vector_store=mock`.
- `GET /health` on intake API returned `ok=true`, `service=chat_gateway`, `used_mcp=false`.
- `POST /api/chat/messages` with `dry_run=true` returned `status=dry_run`, `runner_called=false`, `write_skipped=true`.
- A synthetic formal `ComposedCardV1` job was created under `runtime-test\jobs\merge-e2e-synthetic-formal`.
- `tools/write_lucas_database.py --dry-run` successfully built the ingest request.
- `tools/write_lucas_database.py` then wrote the card to the test Lucas Database API.

### Real Test Write Receipt

```text
status_code: 201
card_id: card_e73adcc3-5e0a-4bb6-a8ed-25d30b4f8b7c
node_id: node_c274ca86-0631-4971-befa-86ebe15512e7
path: /知识卡/合并测试/合并后真实写入测试卡
```

Post-write reads succeeded:

- `GET /api/cards/card_e73adcc3-5e0a-4bb6-a8ed-25d30b4f8b7c`
- `GET /api/nodes/node_c274ca86-0631-4971-befa-86ebe15512e7`
- `GET /api/graph?scope=global&depth=1`

The graph response included the mounted card, parent nodes, concepts, tags, risk, action, methods, source, model, and generated relations.

### Cleanup

Both test services were stopped after validation:

```text
Stopped node process on port 18765
Stopped python process on port 13963
Ports 18765 and 13963 are no longer in use
```

### Boundaries

- No original project files were moved.
- No original Lucas Database file was touched.
- No real link, Douyin, OCR, comments, or model task was run.
- No SiYuan write occurred.
- No MCP was called.
- The only real storage write was the synthetic formal card into the merge-test SQLite database listed above.

## Real Integration Re-Run

Date: 2026-07-02

Run id:

```text
20260702-232957
```

This re-run started the same merged layout on isolated ports and used a fresh test SQLite database:

```text
Lucas Database API: http://127.0.0.1:18765
Intake API:         http://127.0.0.1:13963
Test database:      C:\Users\pppppqr\Desktop\Lucas-Knowledge-System-MergeTest\runtime-test\lucas-merge-test-20260702-232957.db
```

Result:

```text
database health: ok=true, auth.source=env, embedding_provider=mock, vector_store=mock
intake health:   ok=true, used_mcp=false
chat dry-run:    status=dry_run, runner_called=false
writer dry-run:  stage=dry_run
writer real:     stage=completed, status_code=201
card_id:         card_14c59067-21ed-4044-9fad-19ecce953496
node_id:         node_780b1b97-349d-480f-ab4a-07495da8d27a
path:            /知识卡/合并测试/真实验收/合并后真实写入测试卡 20260702-232957
graph readback:  nodes=19, relations=1
```

Post-write readback succeeded through API for card, node, and graph. A direct read-only SQLite check also confirmed that the card and node rows exist with the expected title.

Cleanup:

```text
Ports 18765 and 13963 were released after the test.
```

Boundaries:

- No original project files were moved.
- No original Lucas Database file was touched.
- No real external link, Douyin, OCR, comment, or model task was run.
- No SiYuan write occurred.
- No MCP was called.
- The only real storage write was a synthetic formal card into the fresh merge-test SQLite database listed above.
