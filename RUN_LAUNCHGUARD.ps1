$ErrorActionPreference = 'Stop'
$taskNodeCommand = Get-Command node -ErrorAction SilentlyContinue
if ($taskNodeCommand) {
    $taskNodeExecutable = $taskNodeCommand.Source
} else {
    $taskNodeExecutable = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
}
if (-not (Test-Path -LiteralPath $taskNodeExecutable)) {
    throw 'Node.js 22 or later is required. Install Node, reopen PowerShell, and retry.'
}
& $taskNodeExecutable (Join-Path $PSScriptRoot 'launchguard\server.mjs')
