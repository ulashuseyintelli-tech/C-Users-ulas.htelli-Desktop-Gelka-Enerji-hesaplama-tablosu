# PDSMR-R3B STEP 2 — build-info.json'a artifact SHA256 + derleme komutu +
# bagimlilik surumlerini EKLER (mevcut alanlar KORUNUR - app/main.py bunlari
# `.get(key, default)` ile okur, BILINMEYEN/YENI anahtarlar zararsizdir).
#
# Cagrildigi yerler:
# - build-desktop.bat [PDSMR-R3B STEP 2, backend PyInstaller build'inden HEMEN SONRA]
param(
    [Parameter(Mandatory = $true)][string]$Sha256,
    [Parameter(Mandatory = $true)][string]$VenvPython
)

$ErrorActionPreference = "Stop"

$buildInfoYolu = "build-info.json"
if (-not (Test-Path $buildInfoYolu)) {
    Write-Error "build-info.json bulunamadi: $buildInfoYolu"
    exit 1
}

$j = Get-Content $buildInfoYolu -Raw | ConvertFrom-Json
$j | Add-Member -NotePropertyName "backend_exe_sha256" -NotePropertyValue $Sha256 -Force
$j | Add-Member -NotePropertyName "build_command" -NotePropertyValue "build-desktop.bat (PDSMR-R3B: pyinstaller.exe dogrudan, --paths . YOK)" -Force

$freezeCiktisi = & $VenvPython -m pip freeze
$izlenenPaketler = @("alembic", "sqlalchemy", "fastapi", "pydantic", "pydantic-settings", "uvicorn", "openai", "pillow", "pyinstaller", "playwright")
$depVersiyonlari = [ordered]@{}
foreach ($satir in $freezeCiktisi) {
    if ($satir -match "^([A-Za-z0-9_.\-]+)==(.+)$") {
        $paketAdi = $matches[1].ToLower()
        if ($izlenenPaketler -contains $paketAdi) {
            $depVersiyonlari[$paketAdi] = $matches[2]
        }
    }
}
$j | Add-Member -NotePropertyName "dependency_versions" -NotePropertyValue $depVersiyonlari -Force

# PDSMR-R3B STEP 7 — GERCEK paketlenmis exe testiyle YAKALANDI: "-Encoding
# utf8" Windows PowerShell 5.1'de (build-desktop.bat'in cagirdigi "powershell",
# BU makinede 5.1.26100.9168 - dogrulandi) UTF-8 BOM (EF BB BF) YAZAR.
# app/main.py::/version, build-info.json'u `json.loads(path.read_text(
# encoding="utf-8"))` ile okur - Python'un "utf-8" kodlamasi BOM'u ATLAMAZ,
# bu yuzden json.loads BOM'lu icerikte SESSIZCE JSONDecodeError firlatiyordu
# (main.py'nin genis `except Exception: pass`'ine yakalaniyordu) ve /version
# HER ZAMAN "unknown" donuyordu - Step 7'nin "packaged backend demonstrably
# current" kanitini KORUYORDU. "-Encoding utf8NoBOM" (PS 6+/pwsh) BU
# makinede/cagrida MEVCUT DEGIL (PS 5.1 hata verir) - bu yuzden PS 5.1 VE
# 7+'ta AYNI davranan .NET API'si (UTF8Encoding($false) = BOM YOK) kullanilir.
$jsonMetni = $j | ConvertTo-Json -Depth 5
$buildInfoTamYolu = Join-Path (Get-Location).Path $buildInfoYolu
[System.IO.File]::WriteAllText($buildInfoTamYolu, $jsonMetni, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "build-info.json guncellendi: backend_exe_sha256=$Sha256"
Write-Host "  bagimlilik surumleri: $($depVersiyonlari.Keys -join ', ')"
