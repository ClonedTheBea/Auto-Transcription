param(
    [string]$Config = "config.toml"
)

$Root = $PSScriptRoot
$Logs = Join-Path $Root "logs"
$PidFile = Join-Path $Root "watcher.pid"

New-Item -ItemType Directory -Force -Path $Logs | Out-Null

if (Test-Path -LiteralPath $PidFile) {
    $ExistingPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue
    if ($ExistingPid) {
        $ExistingProcess = Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue
        if ($ExistingProcess) {
            Write-Output "Watcher is already running with PID $ExistingPid."
            exit 0
        }
    }
}

$Python = (Get-Command python).Source
$Runner = Join-Path $Root "watcher_runner.py"
$ConfigPath = $Config
if (-not [System.IO.Path]::IsPathRooted($ConfigPath)) {
    $ConfigPath = Join-Path $Root $ConfigPath
}

Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue

Write-Output "Watcher starting. Keep this window open while you want continuous watching."
Write-Output "Log: $(Join-Path $Logs "pipeline.log")"
& $Python $Runner --config $ConfigPath --pid-file $PidFile
