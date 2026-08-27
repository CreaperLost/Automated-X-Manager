# Stop only the X-Automation Streamlit server started from this repository.

$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ExpectedPython = (Resolve-Path (Join-Path $RepoRoot '.venv\Scripts\python.exe') -ErrorAction SilentlyContinue).Path

$Listeners = @(
    Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
)

if ($Listeners.Count -eq 0) {
    Write-Host 'X-Automation is not running on port 8501.'
    exit 0
}

$Stopped = 0
foreach ($OwnerProcessId in ($Listeners.OwningProcess | Sort-Object -Unique)) {
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId = $OwnerProcessId"
    $IsProjectPython = $ExpectedPython -and ($Process.ExecutablePath -eq $ExpectedPython)
    $IsStreamlitApp = $Process.CommandLine -match '(?i)streamlit' -and
        $Process.CommandLine -match '(?i)src[\\/]x_auto[\\/]app\.py'

    if (-not ($IsProjectPython -and $IsStreamlitApp)) {
        Write-Warning "Refusing to stop PID $OwnerProcessId because it is not this project's Streamlit process."
        continue
    }

    Stop-Process -Id $OwnerProcessId -Force
    Write-Host "Stopped X-Automation (PID $OwnerProcessId)."
    $Stopped += 1
}

if ($Stopped -eq 0) {
    exit 1
}
