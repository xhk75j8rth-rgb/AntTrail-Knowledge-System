param(
  [ValidateSet("codex", "claude", "opencode", "shell")]
  [string] $Adapter = "codex",

  [string] $Cwd,
  [string] $Profile,
  [int] $RunnerTimeoutSec = 900,
  [int] $EntryTimeoutMs = 930000,

  [switch] $Daemon,
  [switch] $UseVendoredCopy
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bridgeRoot = Resolve-Path (Join-Path $scriptDir "..")
$repoRoot = Resolve-Path (Join-Path $bridgeRoot "..")
$intakeRoot = Resolve-Path (Join-Path $repoRoot "intake-control")

if (-not $Cwd) {
  $Cwd = $repoRoot.Path
}

$env:LUCAS_LINK_PROJECT_ROOT = $intakeRoot.Path
$env:LUCAS_LINK_ENTRY_RUNNER_TIMEOUT_SEC = [string] $RunnerTimeoutSec
$env:LUCAS_LINK_ENTRY_TIMEOUT_MS = [string] $EntryTimeoutMs

Write-Host "Lucas WeChat link-intake bridge:"
Write-Host "  LUCAS_LINK_PROJECT_ROOT=$env:LUCAS_LINK_PROJECT_ROOT"
Write-Host "  cwd=$Cwd"
Write-Host "  mode=link_intake"
Write-Host "  underlying_adapter=$Adapter"
Write-Host "  note=CLI-WeChat-Bridge requires an underlying adapter; Lucas URL messages are intercepted before adapter dispatch."
Write-Host "  context_stale_hint=If WeChat replies fail with context stale, send any message from WeChat first, then restart or retry."

if ($Adapter -eq "shell") {
  Write-Warning "Shell adapter executes non-link WeChat text as shell commands. Prefer codex/claude/opencode unless you only send trusted link messages."
}

$argsList = @()

if ($Daemon) {
  $argsList += "--adapter"
  $argsList += $Adapter
}

$argsList += "--cwd"
$argsList += $Cwd

if ($Profile) {
  $argsList += "--profile"
  $argsList += $Profile
}

if ($UseVendoredCopy) {
  $vendorRoot = Resolve-Path (Join-Path $bridgeRoot "vendor\cli-wechat-bridge")
  if (-not (Test-Path -LiteralPath (Join-Path $vendorRoot "node_modules"))) {
    throw "Vendored copy has no node_modules. Run npm install --omit=dev in $vendorRoot or omit -UseVendoredCopy to use the global install."
  }
  $entry = if ($Daemon) {
    Join-Path $vendorRoot "bin\wechat-daemon.mjs"
  } else {
    Join-Path $vendorRoot "bin\wechat-bridge.mjs"
  }
  if (-not $Daemon) {
    $argsList = @("--adapter", $Adapter) + $argsList
  }
  & node $entry @argsList
  exit $LASTEXITCODE
}

if ($Daemon) {
  & wechat-daemon @argsList
  exit $LASTEXITCODE
}

$command = switch ($Adapter) {
  "codex" { "wechat-bridge-codex" }
  "claude" { "wechat-bridge-claude" }
  "opencode" { "wechat-bridge-opencode" }
  "shell" { "wechat-bridge-shell" }
}

& $command @argsList
exit $LASTEXITCODE
