"""
PDSMR-R2I — gelka-rescue.exe icin PyInstaller giris noktasi.

BILEREK ayri bir dosya: `app/legacy_adoption/rescue.py`'nin GORECE
importlari (`from . import policy`) yalniz o modul bir PAKET ICINDEN
import edildiginde calisir. PyInstaller bir script'i dogrudan hedef
alirsa o script `__main__` olarak yuklenir ve paket baglami kaybolur —
gorece importlar patlar. Bu dosya `app.legacy_adoption.rescue`'yi normal
bir MODUL olarak import eder (gorece importlari saglam kalir), yalniz
`_main()`'i cagirir.

Cagrildigi yerler:
- electron/build/build-rescue-helper.bat (PyInstaller --onefile hedefi)
"""
import sys

from app.legacy_adoption.rescue import _main

if __name__ == "__main__":
    sys.exit(_main())
