from __future__ import annotations

"""Canonical fixed hybrid alternatives for a resolution-time mana price."""

from ..activation_mana_cost import fixed_complex_activation_mana_options


HYBRID_RESOLUTION_PAYMENT_KIND = "hybrid_mana"
HYBRID_RESOLUTION_PAYMENT_VERSION = 10
HYBRID_RESOLUTION_PAYMENT_CAPABILITY = "effect.choice.optional_hybrid_mana_payment"
_COLORS = frozenset("WUBRG")


def hybrid_resolution_mana_options(requirements, symbols):
    if not isinstance(symbols, (tuple, list)) or not symbols:
        raise ValueError("A hybrid resolution cost requires fixed hybrid symbols")
    for symbol in symbols:
        if type(symbol) is not str or len(symbol) != 3 or symbol[1] != "/" or symbol[2] not in _COLORS:
            raise ValueError("Resolution payment requires a supported mana-only hybrid symbol")
        if symbol[0] not in _COLORS | {"2"} or symbol[0] == symbol[2]:
            raise ValueError("A hybrid resolution symbol requires two distinct alternatives")
    options = fixed_complex_activation_mana_options(requirements, symbols)
    if len(options) < 2 or any(option.life_payment or option.snow_payment for option in options):
        raise ValueError("Hybrid resolution payment has no closed mana alternatives")
    return options
