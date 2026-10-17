from __future__ import annotations

"""Shared CR 305.6 basic-land subtype to mana-symbol vocabulary."""

BASIC_LAND_MANA=dict(zip('plains island swamp mountain forest'.split(),'WUBRG',strict=True))
LAND_SUBTYPES=frozenset({
    'cave','desert','forest','gate','island','lair','locus','mine','mountain',
    'plains','planet','power-plant','sphere','swamp','tower','town',"urza's",
})
