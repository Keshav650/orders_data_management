$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$python = $null
if (Test-Path "$PSScriptRoot\.venv\Scripts\python.exe") {
    $python = "$PSScriptRoot\.venv\Scripts\python.exe"
} elseif (Test-Path "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe") {
    $python = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
} else {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source }
}

if (-not $python) {
    Write-Error "Python 3.11+ is required to run Amazon Ledger."
}

& $python "$PSScriptRoot\launch.py" @args
