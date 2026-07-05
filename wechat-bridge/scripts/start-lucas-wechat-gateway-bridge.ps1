param(
  [string] $Cwd,
  [string] $GatewayUrl = "http://127.0.0.1:3963/api/chat/messages/async",
  [int] $TimeoutSec = 3600,
  [int] $BatchPollIntervalMs = 2500,
  [int] $BatchTimeoutSec = 3660,
  [switch] $ForceRelogin
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bridgeRoot = Resolve-Path (Join-Path $scriptDir "..")
$repoRoot = Resolve-Path (Join-Path $bridgeRoot "..")
$entry = Join-Path $bridgeRoot "lucas-wechat-gateway-bridge.mjs"
$vendorRoot = Join-Path $bridgeRoot "vendor\cli-wechat-bridge"
$requiredVendorFiles = @(
  "dist\wechat\setup.js",
  "dist\wechat\wechat-transport.js"
)

if (-not $Cwd) {
  $Cwd = $repoRoot.Path
}

if (-not (Test-Path -LiteralPath $entry)) {
  throw "Lucas WeChat gateway bridge entry not found: $entry"
}

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
  throw "Node.js is required to start the Lucas WeChat gateway bridge."
}

function Test-CliWechatBridgeRoot {
  param([string] $Root)

  if (-not $Root) {
    return $false
  }
  $setup = Join-Path $Root "dist\wechat\setup.js"
  $transport = Join-Path $Root "dist\wechat\wechat-transport.js"
  return (Test-Path -LiteralPath $setup) -and (Test-Path -LiteralPath $transport)
}

function Add-CliWechatBridgeRootCandidate {
  param(
    [System.Collections.Generic.List[string]] $Candidates,
    [string] $PathValue
  )

  if (-not $PathValue) {
    return
  }
  $Candidates.Add($PathValue)
  if ((Split-Path -Leaf $PathValue) -ne "cli-wechat-bridge") {
    $Candidates.Add((Join-Path $PathValue "cli-wechat-bridge"))
  }
}

function Resolve-CliWechatBridgeRoot {
  $candidates = [System.Collections.Generic.List[string]]::new()
  Add-CliWechatBridgeRootCandidate $candidates $env:LUCAS_CLI_WECHAT_BRIDGE_ROOT
  Add-CliWechatBridgeRootCandidate $candidates (Join-Path $bridgeRoot "vendor\cli-wechat-bridge")
  Add-CliWechatBridgeRootCandidate $candidates (Join-Path (Split-Path -Parent $node.Source) "node_modules\cli-wechat-bridge")
  Add-CliWechatBridgeRootCandidate $candidates (Join-Path (Split-Path -Parent $node.Source) "node_modules")
  if ($env:NVM_SYMLINK) {
    Add-CliWechatBridgeRootCandidate $candidates (Join-Path $env:NVM_SYMLINK "node_modules\cli-wechat-bridge")
  }
  if ($env:NVM_HOME) {
    Add-CliWechatBridgeRootCandidate $candidates (Join-Path $env:NVM_HOME "node_modules\cli-wechat-bridge")
  }
  if ($env:APPDATA) {
    Add-CliWechatBridgeRootCandidate $candidates (Join-Path $env:APPDATA "npm\node_modules\cli-wechat-bridge")
  }

  $seen = @{}
  $checked = [System.Collections.Generic.List[string]]::new()
  foreach ($candidate in $candidates) {
    if (-not $candidate) {
      continue
    }
    $full = [System.IO.Path]::GetFullPath($candidate)
    $key = $full.ToLowerInvariant()
    if ($seen.ContainsKey($key)) {
      continue
    }
    $seen[$key] = $true
    $checked.Add($full)
    if (Test-CliWechatBridgeRoot $full) {
      return $full
    }
  }

  [Console]::Error.WriteLine(@"
Lucas WeChat gateway bridge optional dependency is not installed.

The GitHub source release does not include copied third-party runtime packages under:
  wechat-bridge/vendor/

Install cli-wechat-bridge globally, copy a compatible package to:
  $vendorRoot

or set:
  LUCAS_CLI_WECHAT_BRIDGE_ROOT=C:\path\to\cli-wechat-bridge

Checked:
  $($checked -join ([Environment]::NewLine + '  '))

See:
  docs/OPTIONAL_DEPENDENCIES.md
  wechat-bridge/VENDOR.md
"@)
  exit 1
}

$cliWechatBridgeRoot = Resolve-CliWechatBridgeRoot

$env:LUCAS_CHAT_GATEWAY_URL = $GatewayUrl
$env:LUCAS_CHAT_GATEWAY_TIMEOUT_SEC = [string] $TimeoutSec
$env:LUCAS_WECHAT_BATCH_POLL_INTERVAL_MS = [string] $BatchPollIntervalMs
$env:LUCAS_WECHAT_BATCH_TIMEOUT_MS = [string] ($BatchTimeoutSec * 1000)
$env:LUCAS_WECHAT_BRIDGE_NAME = "lucas-wechat-gateway-bridge"
$env:LUCAS_WECHAT_FORCE_RELOGIN = if ($ForceRelogin) { "1" } else { "0" }
$env:LUCAS_CLI_WECHAT_BRIDGE_ROOT = $cliWechatBridgeRoot

Write-Host "Lucas WeChat Chat Gateway bridge:"
Write-Host "  mode=chat_gateway"
Write-Host "  gateway_url=$env:LUCAS_CHAT_GATEWAY_URL"
Write-Host "  cwd=$Cwd"
Write-Host "  cli_wechat_bridge_root=$env:LUCAS_CLI_WECHAT_BRIDGE_ROOT"
Write-Host "  handles=links_and_plain_text"
Write-Host "  force_relogin=$env:LUCAS_WECHAT_FORCE_RELOGIN"
Write-Host "  note=WeChat messages are sent to Lucas Chat Gateway. No terminal assistant or shell runtime is started."
Write-Host "  login_hint=If a saved WeChat login exists, no QR is shown. If login is missing or expired, scan the QR output here."

& $node.Source $entry
exit $LASTEXITCODE
