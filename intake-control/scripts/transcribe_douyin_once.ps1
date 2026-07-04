param(
  [Parameter(Mandatory=$true)]
  [string]$Url,

  [switch]$KeepTranscript,

  [switch]$DryRun,

  [switch]$CleanNow
)

$ErrorActionPreference = "Stop"

$DytExe = "C:\Users\pppppqr\tools\douyin-transcriber\dyt.exe"
$WhisperCli = "C:\Users\pppppqr\tools\whisper.cpp\Release\whisper-cli.exe"
$WhisperDir = Split-Path -Parent $WhisperCli
$ModelPath = "C:\Users\pppppqr\.cache\whisper.cpp\models\ggml-base.bin"
$TempRoot = Join-Path $env:TEMP "LucasTranscribe"
$RunId = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), ([guid]::NewGuid().ToString("N").Substring(0, 8))
$RunDir = Join-Path $TempRoot $RunId
$TranscriptPath = Join-Path $RunDir "transcript.txt"

function Write-Log {
  param([string]$Message)
  [Console]::Error.WriteLine($Message)
}

function Test-CommandAvailable {
  param([string]$Name)
  return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Assert-FileExists {
  param(
    [string]$Label,
    [string]$Path
  )
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Label not found: $Path"
  }
}

$createdRunDir = $false

try {
  if ([string]::IsNullOrWhiteSpace($env:TEMP)) {
    throw "TEMP environment variable is empty."
  }

  $dytExists = Test-Path -LiteralPath $DytExe -PathType Leaf
  $whisperExists = Test-Path -LiteralPath $WhisperCli -PathType Leaf
  $modelExists = Test-Path -LiteralPath $ModelPath -PathType Leaf
  $ffmpegAvailable = Test-CommandAvailable "ffmpeg"
  $tempAvailable = Test-Path -LiteralPath $env:TEMP -PathType Container

  Write-Log "Path check:"
  Write-Log "  dyt.exe: $dytExists ($DytExe)"
  Write-Log "  whisper-cli.exe: $whisperExists ($WhisperCli)"
  Write-Log "  model: $modelExists ($ModelPath)"
  Write-Log "  ffmpeg: $ffmpegAvailable"
  Write-Log "  TEMP: $tempAvailable ($env:TEMP)"

  Assert-FileExists "dyt.exe" $DytExe
  Assert-FileExists "whisper-cli.exe" $WhisperCli
  Assert-FileExists "Whisper model" $ModelPath
  if (-not $ffmpegAvailable) {
    throw "ffmpeg not found in PATH."
  }
  if (-not $tempAvailable) {
    throw "TEMP directory not available: $env:TEMP"
  }

  New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
  $createdRunDir = $true
  Write-Log "Run temp dir: $RunDir"

  if ($DryRun) {
    Write-Log "DryRun: checks passed. dyt and whisper were not invoked for transcription."
    return
  }

  $helpText = & $DytExe --help 2>&1 | Out-String
  $supportsLocal = $helpText -match "--local"
  $supportsModelPath = $helpText -match "--model-path"
  $supportsOutput = $helpText -match "--output"

  Write-Log "dyt capability check:"
  Write-Log "  --local: $supportsLocal"
  Write-Log "  --model-path: $supportsModelPath"
  Write-Log "  --output: $supportsOutput"
  Write-Log "  whisper executable selection: via PATH lookup; no --whisper-cli flag found"

  if (-not ($supportsLocal -and $supportsModelPath -and $supportsOutput)) {
    throw "dyt does not expose required local transcription flags. Need adapter changes before real transcription."
  }

  $env:Path = "$WhisperDir;$env:Path"

  Write-Log "Starting one-shot local transcription with dyt."
  Write-Log "Transcript temp path: $TranscriptPath"

  $dytOutput = & $DytExe $Url --local --language zh --model-path $ModelPath --output $TranscriptPath 2>&1
  $exitCode = $LASTEXITCODE
  foreach ($line in $dytOutput) {
    Write-Log $line
  }
  if ($exitCode -ne 0) {
    throw "dyt failed with exit code $exitCode."
  }

  if (-not (Test-Path -LiteralPath $TranscriptPath -PathType Leaf)) {
    throw "dyt completed but transcript file was not created: $TranscriptPath"
  }

  Get-Content -LiteralPath $TranscriptPath -Raw | Write-Output
}
finally {
  if ($createdRunDir -and (Test-Path -LiteralPath $RunDir -PathType Container)) {
    if ($CleanNow) {
      Remove-Item -LiteralPath $RunDir -Recurse -Force -ErrorAction SilentlyContinue
      Write-Log "Cleaned temp dir: $RunDir"
    }
    else {
      if ($KeepTranscript -and (Test-Path -LiteralPath $TranscriptPath -PathType Leaf)) {
        Write-Log "KeepTranscript enabled. Transcript retained at: $TranscriptPath"
      }
      elseif ($KeepTranscript) {
        Write-Log "KeepTranscript enabled, but no transcript file exists."
      }

      Write-Log "Temp dir retained for troubleshooting: $RunDir"
      Write-Log "This temp dir will be cleaned by the daily LucasTempCleanup task when it expires."
    }
  }
}
