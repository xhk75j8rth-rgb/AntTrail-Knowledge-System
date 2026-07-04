param(
  [string[]]$Platforms = @("douyin", "toutiao", "xiaohongshu"),
  [switch]$DryRun,
  [switch]$CloseBrowsers,
  [switch]$IncludeComponentData
)

$ErrorActionPreference = "Stop"

function Write-Log {
  param([string]$Message)
  Write-Output $Message
}

function Format-Bytes {
  param([double]$Bytes)
  if ($Bytes -ge 1GB) { return ("{0:N2} GB" -f ($Bytes / 1GB)) }
  if ($Bytes -ge 1MB) { return ("{0:N2} MB" -f ($Bytes / 1MB)) }
  if ($Bytes -ge 1KB) { return ("{0:N2} KB" -f ($Bytes / 1KB)) }
  return ("{0:N0} B" -f $Bytes)
}

function Get-ItemStats {
  param([System.IO.FileSystemInfo]$Item)
  if ($Item.PSIsContainer) {
    $files = @(Get-ChildItem -LiteralPath $Item.FullName -Recurse -File -Force -ErrorAction SilentlyContinue)
    $bytes = ($files | Measure-Object -Property Length -Sum).Sum
    return [pscustomobject]@{
      FileCount = $files.Count
      Bytes = [double]($bytes -as [double])
    }
  }
  return [pscustomobject]@{
    FileCount = 1
    Bytes = [double]$Item.Length
  }
}

function Test-PathInside {
  param(
    [string]$Child,
    [string]$Parent
  )
  $childFull = [System.IO.Path]::GetFullPath($Child).TrimEnd('\')
  $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\')
  return (
    $childFull.Equals($parentFull, [System.StringComparison]::OrdinalIgnoreCase) -or
    $childFull.StartsWith($parentFull + "\", [System.StringComparison]::OrdinalIgnoreCase)
  )
}

function Get-ProfileProcesses {
  param([string]$ProfilePath)
  $needle = [System.IO.Path]::GetFullPath($ProfilePath).TrimEnd('\')
  return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and
      $_.CommandLine.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
  throw "LOCALAPPDATA is not available."
}

$localAppData = (Resolve-Path -LiteralPath $env:LOCALAPPDATA).Path.TrimEnd('\')
$profileRoot = Join-Path $localAppData "LucasKnowledgeDB\browser_profiles"
$profileRootFull = [System.IO.Path]::GetFullPath($profileRoot).TrimEnd('\')

if (-not (Test-PathInside $profileRootFull $localAppData)) {
  throw "Refusing to inspect browser profiles outside LOCALAPPDATA: $profileRootFull"
}

$allowedPlatforms = @("douyin", "toutiao", "xiaohongshu", "webpage")
$requestedPlatforms = @($Platforms | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })
foreach ($platform in $requestedPlatforms) {
  if ($allowedPlatforms -notcontains $platform) {
    throw "Unsupported platform profile: $platform"
  }
}

$cacheRelativePaths = @(
  "BrowserMetrics",
  "GrShaderCache",
  "ShaderCache",
  "GPUCache",
  "DawnCache",
  "DawnGraphiteCache",
  "DawnWebGPUCache",
  "GPUPersistentCache",
  "component_crx_cache",
  "extensions_crx_cache",
  "Default\Cache",
  "Default\Code Cache",
  "Default\GPUCache",
  "Default\Media Cache",
  "Default\DawnCache",
  "Default\DawnGraphiteCache",
  "Default\DawnWebGPUCache",
  "Default\Service Worker\CacheStorage",
  "Default\Service Worker\ScriptCache",
  "Default\blob_storage"
)

$componentRelativePaths = @(
  "ProvenanceData",
  "ProvenanceDataTensors"
)

if ($IncludeComponentData) {
  $cacheRelativePaths += $componentRelativePaths
}

Write-Log "Lucas browser profile cache cleanup"
Write-Log "Mode: $(if ($DryRun) { 'DryRun' } else { 'Delete' })"
Write-Log "Close browsers: $(if ($CloseBrowsers) { 'yes' } else { 'no' })"
Write-Log "Include component data: $(if ($IncludeComponentData) { 'yes' } else { 'no' })"
Write-Log "Profile root: $profileRootFull"

if (-not (Test-Path -LiteralPath $profileRootFull -PathType Container)) {
  Write-Log "Profile root missing, nothing to clean."
  return
}

foreach ($platform in $requestedPlatforms) {
  $profilePath = Join-Path $profileRootFull $platform
  $profileFull = [System.IO.Path]::GetFullPath($profilePath).TrimEnd('\')

  Write-Log ""
  Write-Log "Profile: $platform"
  Write-Log "Root: $profileFull"

  if (-not (Test-PathInside $profileFull $profileRootFull)) {
    throw "Refusing to clean profile outside Lucas browser profile root: $profileFull"
  }
  if (-not (Test-Path -LiteralPath $profileFull -PathType Container)) {
    Write-Log "  Missing, skipped."
    continue
  }

  $processes = Get-ProfileProcesses $profileFull
  if ($processes.Count -gt 0) {
    $ids = ($processes | Select-Object -ExpandProperty ProcessId | Sort-Object -Unique) -join ","
    if (-not $CloseBrowsers) {
      Write-Log "  Active browser processes found: $ids"
      Write-Log "  Skipped. Re-run with -CloseBrowsers to close only this Lucas profile before cleanup."
      continue
    }
    if ($DryRun) {
      Write-Log "  Would close browser processes: $ids"
    }
    else {
      foreach ($proc in $processes) {
        try {
          Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
          Write-Log "  Closed process: $($proc.ProcessId)"
        }
        catch {
          Write-Log "  Failed to close process: $($proc.ProcessId) | error=$($_.Exception.Message)"
        }
      }
      Start-Sleep -Seconds 2
    }
  }

  $candidateMap = @{}
  foreach ($relative in $cacheRelativePaths) {
    $candidate = Join-Path $profileFull $relative
    if (Test-Path -LiteralPath $candidate) {
      $candidateMap[[System.IO.Path]::GetFullPath($candidate).TrimEnd('\')] = $true
    }
  }

  $defaultStorage = Join-Path $profileFull "Default\Storage"
  if (Test-Path -LiteralPath $defaultStorage -PathType Container) {
    Get-ChildItem -LiteralPath $defaultStorage -Recurse -Directory -Force -ErrorAction SilentlyContinue |
      Where-Object {
        $_.Name -in @("Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache")
      } |
      ForEach-Object {
        $candidateMap[[System.IO.Path]::GetFullPath($_.FullName).TrimEnd('\')] = $true
      }
  }

  $candidates = @($candidateMap.Keys | Sort-Object)
  if ($candidates.Count -eq 0) {
    Write-Log "  No cache directories found."
    continue
  }

  foreach ($candidate in $candidates) {
    if (-not (Test-PathInside $candidate $profileFull)) {
      Write-Log "  Refused: $candidate"
      continue
    }
    $item = Get-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue
    if (-not $item) {
      continue
    }
    $stats = Get-ItemStats $item
    if ($DryRun) {
      Write-Log ("  Would delete: {0} | files={1} | size={2}" -f $candidate, $stats.FileCount, (Format-Bytes $stats.Bytes))
      continue
    }
    try {
      Remove-Item -LiteralPath $candidate -Recurse -Force -ErrorAction Stop
      Write-Log ("  Deleted: {0} | files={1} | freed={2} | success=True" -f $candidate, $stats.FileCount, (Format-Bytes $stats.Bytes))
    }
    catch {
      Write-Log ("  Delete failed: {0} | files={1} | size={2} | success=False | error={3}" -f $candidate, $stats.FileCount, (Format-Bytes $stats.Bytes), $_.Exception.Message)
    }
  }
}
