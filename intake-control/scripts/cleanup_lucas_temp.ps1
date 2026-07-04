param(
  [int]$OlderThanHours = 36,
  [switch]$DryRun
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

if ([string]::IsNullOrWhiteSpace($env:TEMP) -or -not (Test-Path -LiteralPath $env:TEMP -PathType Container)) {
  throw "TEMP directory is not available: $env:TEMP"
}

if ($OlderThanHours -lt 1) {
  throw "OlderThanHours must be >= 1."
}

$tempRoot = (Resolve-Path -LiteralPath $env:TEMP).Path.TrimEnd('\')
$allowedRoots = @(
  (Join-Path $tempRoot "LucasTranscribe"),
  (Join-Path $tempRoot "LucasVideoOCR"),
  (Join-Path $tempRoot "LucasOCRTest"),
  (Join-Path $tempRoot "LucasTranscribeWhisperTest"),
  (Join-Path $tempRoot "LucasWebImageOCR"),
  (Join-Path $tempRoot "LucasCommentBrowser"),
  (Join-Path $tempRoot "LucasDouyinVideoSource"),
  (Join-Path $tempRoot "LucasTranscribeSetup")
)

$allowedRoots += @(
  Get-ChildItem -LiteralPath $tempRoot -Directory -Force -ErrorAction SilentlyContinue |
    Where-Object {
      $_.Name -like "LucasToutiaoArticle-*" -or
      $_.Name -like "LucasXiaohongshuNote-*"
    } |
    ForEach-Object { $_.FullName }
)

$blockedRoots = @(
  "C:\Users\pppppqr\tools",
  "C:\Users\pppppqr\.cache",
  "C:\Users\pppppqr\Desktop\Lucas-SiYuan-Codex"
)

$cutoff = (Get-Date).AddHours(-1 * $OlderThanHours)
Write-Log "Lucas temp cleanup"
Write-Log "Mode: $(if ($DryRun) { 'DryRun' } else { 'Delete' })"
Write-Log "Older than hours: $OlderThanHours"
Write-Log "Cutoff: $cutoff"

foreach ($root in $allowedRoots) {
  $rootFull = [System.IO.Path]::GetFullPath($root).TrimEnd('\')

  foreach ($blocked in $blockedRoots) {
    $blockedFull = [System.IO.Path]::GetFullPath($blocked).TrimEnd('\')
    if ($rootFull.Equals($blockedFull, [System.StringComparison]::OrdinalIgnoreCase) -or
        $rootFull.StartsWith($blockedFull + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to clean blocked path: $rootFull"
    }
  }

  if (-not $rootFull.StartsWith($tempRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean non-TEMP path: $rootFull"
  }

  Write-Log ""
  Write-Log "Root: $rootFull"
  if (-not (Test-Path -LiteralPath $rootFull -PathType Container)) {
    Write-Log "  Missing, skipped."
    continue
  }

  $candidates = @(Get-ChildItem -LiteralPath $rootFull -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt $cutoff })

  if ($candidates.Count -eq 0) {
    Write-Log "  No expired files or directories."
    continue
  }

  foreach ($item in $candidates) {
    $itemFull = [System.IO.Path]::GetFullPath($item.FullName)
    if (-not $itemFull.StartsWith($rootFull + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
      Write-Log "  Refused: $itemFull"
      continue
    }

    $stats = Get-ItemStats $item
    if ($DryRun) {
      Write-Log ("  Would delete: {0} | files={1} | size={2}" -f $item.FullName, $stats.FileCount, (Format-Bytes $stats.Bytes))
      continue
    }

    $success = $false
    try {
      Remove-Item -LiteralPath $item.FullName -Recurse -Force -ErrorAction Stop
      $success = $true
    }
    catch {
      Write-Log ("  Delete failed: {0} | files={1} | size={2} | success=False | error={3}" -f $item.FullName, $stats.FileCount, (Format-Bytes $stats.Bytes), $_.Exception.Message)
      continue
    }

    Write-Log ("  Deleted: {0} | files={1} | freed={2} | success={3}" -f $item.FullName, $stats.FileCount, (Format-Bytes $stats.Bytes), $success)
  }
}
