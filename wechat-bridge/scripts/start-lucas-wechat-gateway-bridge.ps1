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

$env:LUCAS_CHAT_GATEWAY_URL = $GatewayUrl
$env:LUCAS_CHAT_GATEWAY_TIMEOUT_SEC = [string] $TimeoutSec
$env:LUCAS_WECHAT_BATCH_POLL_INTERVAL_MS = [string] $BatchPollIntervalMs
$env:LUCAS_WECHAT_BATCH_TIMEOUT_MS = [string] ($BatchTimeoutSec * 1000)
$env:LUCAS_WECHAT_BRIDGE_NAME = "lucas-wechat-gateway-bridge"
$env:LUCAS_WECHAT_FORCE_RELOGIN = if ($ForceRelogin) { "1" } else { "0" }

Write-Host "Lucas WeChat Chat Gateway bridge:"
Write-Host "  mode=chat_gateway"
Write-Host "  gateway_url=$env:LUCAS_CHAT_GATEWAY_URL"
Write-Host "  cwd=$Cwd"
Write-Host "  handles=links_and_plain_text"
Write-Host "  force_relogin=$env:LUCAS_WECHAT_FORCE_RELOGIN"
Write-Host "  note=WeChat messages are sent to Lucas Chat Gateway. No terminal assistant or shell runtime is started."
Write-Host "  login_hint=If a saved WeChat login exists, no QR is shown. If login is missing or expired, scan the QR output here."

& $node.Source $entry
exit $LASTEXITCODE
