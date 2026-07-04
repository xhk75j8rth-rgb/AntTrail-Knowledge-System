$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

function New-IntakeVenv {
    param(
        [Parameter(Mandatory = $true)]
        [string] $VenvPath
    )

    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($PyLauncher) {
        & $PyLauncher.Source -3 -m venv $VenvPath
        return
    }

    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) {
        & $Python.Source -m venv $VenvPath
        return
    }

    throw "Python 3.10+ is required. Install Python or make python/py available in PATH."
}

function Copy-ExampleConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Source,
        [Parameter(Mandatory = $true)]
        [string] $Destination
    )

    if ((Test-Path -LiteralPath $Source) -and -not (Test-Path -LiteralPath $Destination)) {
        Copy-Item -LiteralPath $Source -Destination $Destination
        Write-Host "Created local config: $Destination"
    }
}

function Ensure-LocalEnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string] $EnvPath,
        [Parameter(Mandatory = $true)]
        [string] $Key,
        [Parameter(Mandatory = $true)]
        [string] $Value
    )

    $Lines = @()
    if (Test-Path -LiteralPath $EnvPath) {
        $Lines = Get-Content -LiteralPath $EnvPath
    }

    foreach ($Line in $Lines) {
        if ($Line -match "^\s*$([regex]::Escape($Key))\s*=") {
            return
        }
    }

    $Parent = Split-Path -Parent $EnvPath
    if ($Parent -and -not (Test-Path -LiteralPath $Parent)) {
        New-Item -ItemType Directory -Path $Parent | Out-Null
    }

    Add-Content -LiteralPath $EnvPath -Value "$Key=$Value"
    Write-Host "Added local development value: $Key"
}

Write-Host "Installing AntTrail Database dependencies..."
Push-Location (Join-Path $Root "lucas-database")
try {
    npm install
}
finally {
    Pop-Location
}

Write-Host "Creating intake-control Python virtual environment..."
$IntakeRoot = Join-Path $Root "intake-control"
$VenvPython = Join-Path $IntakeRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    New-IntakeVenv -VenvPath (Join-Path $IntakeRoot ".venv")
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $IntakeRoot "requirements.txt")

Write-Host "Preparing local intake config files..."
$ConfigRoot = Join-Path $IntakeRoot "config"
Copy-ExampleConfig `
    -Source (Join-Path $ConfigRoot "ai_layer.example.json") `
    -Destination (Join-Path $ConfigRoot "ai_layer.local.json")
Copy-ExampleConfig `
    -Source (Join-Path $ConfigRoot "storage.example.json") `
    -Destination (Join-Path $ConfigRoot "storage.local.json")
Copy-ExampleConfig `
    -Source (Join-Path $ConfigRoot "link_pipeline.example.json") `
    -Destination (Join-Path $ConfigRoot "link_pipeline.json")
Ensure-LocalEnvValue `
    -EnvPath (Join-Path $IntakeRoot ".env") `
    -Key "LUCAS_DB_API_KEY" `
    -Value "lucas-local-dev-token"

Write-Host ""
Write-Host "Setup complete."
Write-Host "Start Database: .\scripts\dev-database.ps1"
Write-Host "Start Intake UI: .\scripts\dev-intake.ps1"
