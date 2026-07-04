param(
  [Parameter(Mandatory = $true)]
  [string] $Message,

  [string] $ConversationId = "wechat-bridge",
  [string] $SenderId = "wechat-user",
  [string] $SenderName = "WeChat User",

  [switch] $ExtractOnly
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$intakeRoot = Resolve-Path (Join-Path $repoRoot "intake-control")
$entryScript = Join-Path $intakeRoot "tools\wechat_link_entry.py"

$argsList = @(
  $entryScript,
  "--message", $Message,
  "--json",
  "--conversation-id", $ConversationId,
  "--sender-id", $SenderId,
  "--sender-name", $SenderName
)

if ($ExtractOnly) {
  $argsList += "--extract-only"
}

Push-Location $intakeRoot
try {
  & python @argsList
} finally {
  Pop-Location
}
