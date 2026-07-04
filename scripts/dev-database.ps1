$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\lucas-database")
Push-Location $ProjectRoot
try {
    npm run dev
}
finally {
    Pop-Location
}
