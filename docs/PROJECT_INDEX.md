# Project Index

## Intake Control

Path: `intake-control/`

Read first:

- `README.md`
- `docs/INTAKE_SYSTEM_RULES.md`
- `docs/CHAT_GATEWAY_ABSTRACTION.md`
- `docs/LUCAS_DATABASE_SINK.md`

Key commands:

```powershell
Push-Location .\intake-control
python -m uvicorn server.chat_api:app --host 127.0.0.1 --port 3963
Pop-Location
```

## Lucas Database

Path: `lucas-database/`

Read first:

- `README.md`
- `docs/API.md`
- `docs/DATA_MODEL.md`
- `docs/CARD_INGEST_API.md`

Key commands:

```powershell
Push-Location .\lucas-database
npm install
npm run dev
Pop-Location
```

## Integration Boundary

```text
intake-control runtime job
-> ComposedCardV1
-> quality gate
-> tools/write_lucas_database.py
-> Lucas Database POST /api/cards/ingest
```
