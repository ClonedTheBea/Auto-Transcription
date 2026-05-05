$Root = $PSScriptRoot
$PidFile = Join-Path $Root "watcher.pid"
$PipelineLog = Join-Path $Root "logs\pipeline.log"

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
    Write-Output "Watcher is running with PID $PidValue."
    $Children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $PidValue"
    foreach ($Child in $Children) {
        Write-Output "Child process: PID $($Child.ProcessId) $($Child.Name)"
    }
    if (Test-Path -LiteralPath $PipelineLog) {
        Write-Output "Recent pipeline log:"
        Get-Content -LiteralPath $PipelineLog -Tail 10
    }
} else {
    Write-Output "Watcher PID file exists, but the process is not running."
}
