$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\intake-control")
Push-Location $ProjectRoot
try {
    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
    & $Python -m uvicorn server.chat_api:app --host 127.0.0.1 --port 3963
}
finally {
    Pop-Location
}
