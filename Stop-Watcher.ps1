$Root = $PSScriptRoot
$PidFile = Join-Path $Root "watcher.pid"

if (-not (Test-Path -LiteralPath $PidFile)) {
    Write-Output "Watcher is not running. No watcher.pid file found."
    exit 0
}

$PidValue = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue
$Process = $null
if ($PidValue) {
    $Process = Get-Process -Id $PidValue -ErrorAction SilentlyContinue
}

if ($Process) {
    Stop-Process -Id $PidValue -Force
    Write-Output "Watcher stopped."
} else {
    Write-Output "Watcher process was not running."
}

Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
