param(
  [string] $Message = "https://example.com",
  [switch] $Json
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bridgeRoot = Resolve-Path (Join-Path $scriptDir "..")
$repoRoot = Resolve-Path (Join-Path $bridgeRoot "..")
$intakeRoot = Resolve-Path (Join-Path $repoRoot "intake-control")
$entryScript = Join-Path $intakeRoot "tools\wechat_link_entry.py"

$argsList = @(
  $entryScript,
  "--message", $Message,
  "--extract-only",
  "--conversation-id", "wechat-bridge-test",
  "--sender-id", "wechat-test-user",
  "--sender-name", "WeChat Test User"
)

if ($Json) {
  $argsList += "--json"
}

Push-Location $intakeRoot
try {
  & python @argsList
} finally {
  Pop-Location
}
