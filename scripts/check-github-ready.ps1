$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Git = Get-Command git -ErrorAction SilentlyContinue
if (-not $Git) {
    throw "Git is required for this check."
}

$RequiredFiles = @(
    "README.md",
    ".gitignore",
    "scripts/setup-dev.ps1",
    "scripts/dev-database.ps1",
    "scripts/dev-intake.ps1",
    "intake-control/requirements.txt",
    "intake-control/config/ai_layer.example.json",
    "intake-control/config/storage.example.json",
    "intake-control/config/link_pipeline.example.json",
    "lucas-database/package.json",
    "lucas-database/package-lock.json"
)

$Missing = @()
foreach ($RelativePath in $RequiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $Root $RelativePath))) {
        $Missing += $RelativePath
    }
}

if ($Missing.Count -gt 0) {
    Write-Host "Missing required release files:" -ForegroundColor Red
    $Missing | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    exit 1
}

$TempGitDir = Join-Path ([System.IO.Path]::GetTempPath()) ("anttrail-gitcheck-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TempGitDir | Out-Null

try {
    & $Git.Source --git-dir $TempGitDir --work-tree $Root init --quiet
    $CandidateFiles = & $Git.Source --git-dir $TempGitDir --work-tree $Root ls-files --others --cached --exclude-standard
}
finally {
    $ResolvedTemp = Resolve-Path -LiteralPath $TempGitDir -ErrorAction SilentlyContinue
    $ResolvedSystemTemp = Resolve-Path -LiteralPath ([System.IO.Path]::GetTempPath())
    if ($ResolvedTemp -and $ResolvedTemp.Path.StartsWith($ResolvedSystemTemp.Path, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $ResolvedTemp.Path -Recurse -Force
    }
}

$ForbiddenPatterns = @(
    '(^|/)node_modules/',
    '(^|/)\.venv/',
    '(^|/)\.venv-bge-m3/',
    '(^|/)data/',
    '(^|/)runtime/',
    '(^|/)runtime-test/',
    '(^|/)runtime-restart-logs/',
    '(^|/)\.agents/',
    '(^|/)wechat-bridge/vendor/',
    '(^|/)AGENTS\.md$',
    '(^|/)AGENT_[^/]+\.md$',
    '(^|/)HANDOFF\.md$',
    '\.env$',
    '\.local\.json$',
    '\.db$',
    '\.db-shm$',
    '\.db-wal$',
    '\.sqlite$',
    '\.log$',
    '\.pid$',
    '\.pyc$',
    '\.tsbuildinfo$'
)

$Blocked = @()
foreach ($File in $CandidateFiles) {
    $Normalized = ($File -replace '\\', '/')
    foreach ($Pattern in $ForbiddenPatterns) {
        if ($Normalized -match $Pattern) {
            $Blocked += $Normalized
            break
        }
    }
}

if ($Blocked.Count -gt 0) {
    Write-Host "These files would be included but should stay local:" -ForegroundColor Red
    $Blocked | Sort-Object | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    exit 1
}

Write-Host "GitHub source check passed." -ForegroundColor Green
Write-Host ("Candidate source files: {0}" -f ($CandidateFiles | Measure-Object).Count)
Write-Host "Local data, dependencies, logs, private config, and agent memory are excluded."
