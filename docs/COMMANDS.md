# Commands

Run these from the merge-test root.

## Intake

```powershell
.\scripts\dev-intake.ps1
```

Health:

```powershell
Invoke-RestMethod http://127.0.0.1:3963/health
```

## Lucas Database

Install dependencies if needed:

```powershell
Push-Location .\lucas-database
npm install
Pop-Location
```

Start:

```powershell
.\scripts\dev-database.ps1
```

Health:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
```

## Notes

This merge test excludes machine-local data and dependency folders. If `node_modules` is absent, run `npm install` in `lucas-database/` before database typecheck or dev startup.
