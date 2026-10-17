from __future__ import annotations

"""Materialize intrinsic mana before layer-six ability changes."""

from typing import Iterable, Sequence

from .abilities import ActivatedAbility
from .basic_land_types import BASIC_LAND_MANA
from .fixed_mana_abilities import FixedManaMode
from .util import normalize_mana_bundle


def intrinsic_land_mana_abilities(
    card_types: Iterable[str],
    subtypes: Iterable[str],
    *,
    existing_abilities: Sequence[ActivatedAbility] = (),
) -> tuple[ActivatedAbility, ...]:
    if 'land' not in {value.casefold() for value in card_types}:
        return ()
    current = {value.casefold() for value in subtypes}
    ability_ids = {ability.ability_id for ability in existing_abilities}
    result = []
    for subtype, color in BASIC_LAND_MANA.items():
        if subtype not in current:
            continue
        ability_id = f'intrinsic_{subtype}'
        if ability_id in ability_ids:
            raise ValueError(
                f'Activated ability id {ability_id} is reserved for CR 305.6'
            )
        result.append(ActivatedAbility(
            ability_id=ability_id,
            line_index=20_000 + len(existing_abilities) + len(result),
            oracle_line=f'{{T}}: Add {{{color}}}.',
            cost_text='{T}',
            effect_text=f'Add {{{color}}}.',
            zones=('battlefield',),
            mana=normalize_mana_bundle(None),
            tap_source=True,
            mana_ability=True,
            fixed_mana_outputs=(FixedManaMode.from_bundle({color: 1}),),
        ))
    return tuple(result)
