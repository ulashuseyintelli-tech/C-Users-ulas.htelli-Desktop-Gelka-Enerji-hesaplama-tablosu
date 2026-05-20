# Mode B: Dev backend with ISOLATED EMPTY DB (no PTF/YEKDEM data)
# Used for S-06 and S-07 (market-data-missing scenarios)
# Usage: powershell -ExecutionPolicy Bypass -File .kiro/operational-acceptance/phase-a/scripts/start_backend_mode_b_empty.ps1
# Cwd must be repo root.

$ErrorActionPreference = 'Stop'

# Empty test DB lives OUTSIDE backend/ to avoid any chance of overwriting production
$emptyDbPath = '.kiro/operational-acceptance/phase-a/test_db/empty_market_data.db'
$absDbPath = Join-Path -Path (Get-Location) -ChildPath $emptyDbPath
$absDbDir = Split-Path -Parent $absDbPath
if (-not (Test-Path $absDbDir)) { New-Item -ItemType Directory -Force -Path $absDbDir | Out-Null }

# Always start fresh — delete any prior copy so PTF/YEKDEM are guaranteed empty
if (Test-Path $absDbPath) {
    Write-Host "Removing prior empty test DB: $absDbPath"
    Remove-Item -Force $absDbPath
}

# Production DB safety check: must NOT be the path we hand to uvicorn
$prodDb = (Resolve-Path 'backend/gelka_enerji.db').Path
if ($absDbPath -eq $prodDb) {
    Write-Error "REFUSING TO START: empty DB path matches production DB path."
    exit 1
}

Push-Location backend
try {
    # Use absolute URI to be safe; SQLAlchemy needs forward slashes
    $uriPath = ($absDbPath -replace '\\', '/')
    $env:DATABASE_URL = "sqlite:///$uriPath"
    $env:RECON_DB_MODE = 'isolated_empty'
    Write-Host "DATABASE_URL = $env:DATABASE_URL"
    Write-Host "(Production DB at backend/gelka_enerji.db is UNTOUCHED)"
    Write-Host "Starting uvicorn on port 8000..."
    python -m uvicorn app.main:app --reload --port 8000 --host 127.0.0.1
} finally {
    Pop-Location
}
