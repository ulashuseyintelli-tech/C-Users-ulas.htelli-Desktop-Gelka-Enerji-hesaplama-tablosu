# Mode A: Dev backend with PRODUCTION DB
# Usage: powershell -ExecutionPolicy Bypass -File .kiro/operational-acceptance/phase-a/scripts/start_backend_mode_a.ps1
# Cwd must be repo root.

$ErrorActionPreference = 'Stop'
Push-Location backend
try {
    # Production DB path (relative to backend/)
    $env:DATABASE_URL = 'sqlite:///./gelka_enerji.db'
    $env:RECON_DB_MODE = 'production'
    Write-Host "DATABASE_URL = $env:DATABASE_URL"
    Write-Host "Starting uvicorn on port 8000..."
    python -m uvicorn app.main:app --reload --port 8000 --host 127.0.0.1
} finally {
    Pop-Location
}
