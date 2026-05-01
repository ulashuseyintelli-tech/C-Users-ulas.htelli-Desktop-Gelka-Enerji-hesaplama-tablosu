"""
Pricing Risk Engine — Dengesizlik Maliyeti Hesaplama Motoru.

İki mod desteklenir:
- SMF bazlı: |Ağırlıklı_SMF − Ağırlıklı_PTF| × forecast_error_rate
- Sabit oran: imbalance_cost_tl_per_mwh × forecast_error_rate

Sonuç TL/MWh birimindedir.

Requirements: 8.3, 8.4, 8.5
"""

from __future__ import annotations

from .models import ImbalanceParams


def calculate_imbalance_cost(
    weighted_ptf: float,
    weighted_smf: float,
    params: ImbalanceParams,
) -> float:
    """Dengesizlik maliyeti hesapla (TL/MWh).

    Args:
        weighted_ptf: Ağırlıklı PTF (TL/MWh).
        weighted_smf: Ağırlıklı SMF (TL/MWh).
        params: Dengesizlik parametreleri.

    Returns:
        Dengesizlik maliyeti (TL/MWh).
    """
    if params.smf_based_imbalance_enabled:
        # SMF bazlı mod: |Ağırlıklı_SMF − Ağırlıklı_PTF| × forecast_error_rate
        return abs(weighted_smf - weighted_ptf) * params.forecast_error_rate
    else:
        # Sabit oran modu: imbalance_cost_tl_per_mwh × forecast_error_rate
        return params.imbalance_cost_tl_per_mwh * params.forecast_error_rate
