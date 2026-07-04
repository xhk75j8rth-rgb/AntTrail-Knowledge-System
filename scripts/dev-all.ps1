$ErrorActionPreference = "Stop"
$ScriptsRoot = $PSScriptRoot
Start-Process powershell -WindowStyle Hidden -ArgumentList "-ExecutionPolicy", "Bypass", "-File", (Join-Path $ScriptsRoot "dev-intake.ps1")
Start-Process powershell -WindowStyle Hidden -ArgumentList "-ExecutionPolicy", "Bypass", "-File", (Join-Path $ScriptsRoot "dev-database.ps1")
