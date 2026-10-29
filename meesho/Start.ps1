$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$localPython = Join-Path $projectDir '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $localPython) { $pythonExe = $localPython }
elseif (Test-Path -LiteralPath $bundledPython) { $pythonExe = $bundledPython }
else { $pythonExe = (Get-Command python -ErrorAction Stop).Source }
& $pythonExe (Join-Path $projectDir 'launch.py') @args
if ($LASTEXITCODE -ne 0) {
    Read-Host 'Startup failed. Press Enter to close'
    exit 1
}
