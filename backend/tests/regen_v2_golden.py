"""
S5-R02B — v2 golden snapshot'i yeniden uretme YARDIMCI KOMUTU (test degil).

Onceden bu islem `RECON_V2_GOLDEN_REGEN=1` bayrakli bir skipif-test'ti ve
her tam kosuya kalici 1 skip ekliyordu. Regen bir dogrulama degil, bir
YAZMA islemidir; bu yuzden acik opt-in komuta tasindi:

    python -m tests.regen_v2_golden          (backend/ dizininden)

Sonrasinda diff'i inceleyin ve kasitliysa commit edin. Golden ESITLIGI her
kosuda test_recon_v2_golden.py'nin normal testleriyle dogrulanir.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.test_recon_v2_golden import regenerate_v2_golden_snapshot  # noqa: E402

if __name__ == "__main__":
    yol = regenerate_v2_golden_snapshot()
    print(f"Golden snapshot yeniden uretildi: {yol}")
    print("Diff'i inceleyin; kasitliysa commit edin.")
