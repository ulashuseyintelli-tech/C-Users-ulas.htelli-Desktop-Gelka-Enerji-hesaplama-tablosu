# lint-task-markers.ps1 - CI guard for tasks.md marker format
# Exit: 0=clean, 1=violation
# Rules: R1-R6 (see DASHBOARD.md)
$ErrorActionPreference = "Stop"
$exitCode = 0
$warnCount = 0
$files = Get-ChildItem -Path ".kiro/specs/*/tasks.md" -ErrorAction SilentlyContinue
if (-not $files) { Write-Host "WARN: no tasks.md found"; exit 0 }
foreach ($f in $files) {
    $spec = $f.Directory.Name
    $lines = Get-Content $f.FullName
    $n = 0
    $inD = $false
    foreach ($line in $lines) {
        $n++
        if ($line -match '^- \[d\]') { $inD = $true }
        elseif ($line -match '^- \[(x|\-| )\]') { $inD = $false }
        $isSoft = $line -match '^\s*- \[ \]\*'
        $hasTag = $line -match '\{SOFT:(SAFETY|NICE)\}'
        $hasSoftPfx = $line -match '\{SOFT:'
        if ($isSoft -and (-not $hasTag)) {
            Write-Host "FAIL [$spec] L$n R1: [ ]* missing SOFT tag" -ForegroundColor Red
            $exitCode = 1
        }
        if ($hasTag -and (-not $isSoft)) {
            Write-Host "FAIL [$spec] L$n R2: SOFT tag on non-[ ]* line" -ForegroundColor Red
            $exitCode = 1
        }
        if ($hasSoftPfx -and (-not $hasTag)) {
            Write-Host "FAIL [$spec] L$n R4: invalid SOFT value" -ForegroundColor Red
            $exitCode = 1
        }
        if ($line -match '^\s*- \[ \] \*') {
            Write-Host "FAIL [$spec] L$n R5: space-star on hard line" -ForegroundColor Red
            $exitCode = 1
        }
        if ($inD -and ($line -match '^\s{2,}- \[ \](?!\*)')) {
            Write-Host "WARN [$spec] L$n R6 (expected): hard [ ] child under [d] deferred scope" -ForegroundColor Yellow
            $warnCount++
        }
    }
}
if ($exitCode -eq 0 -and $warnCount -eq 0) {
    Write-Host "OK: all tasks.md pass marker standard" -ForegroundColor Green
} elseif ($exitCode -eq 0 -and $warnCount -gt 0) {
    Write-Host "OK with $warnCount warnings (deferred scope)" -ForegroundColor Yellow
}
exit $exitCode