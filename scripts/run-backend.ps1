param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot '.env'
if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw 'Copy .env.example to .env in the project root before starting.'
}
if (Test-Path -LiteralPath $Python) { $Python = (Resolve-Path -LiteralPath $Python).Path }
foreach ($line in Get-Content -LiteralPath $EnvFile) {
    if ($line -match '^([A-Z][A-Z0-9_]*)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}
Push-Location (Join-Path $ProjectRoot 'backend')
try { & $Python manage.py runserver 127.0.0.1:8000 --noreload; exit $LASTEXITCODE }
finally { Pop-Location }
