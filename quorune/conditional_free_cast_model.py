from __future__ import annotations

CONDITIONAL_FREE_CAST_CAPABILITY = 'casting.alternative_cost.conditional_zero_mana'
FREE_CONDITION_KINDS = frozenset({'land_pair', 'controlled_commander'})
BASIC_TYPES = frozenset({'forest', 'island', 'mountain', 'plains', 'swamp'})


def validate_free_cast_condition(*, kind: str, own_land, opponent_land, schema_version):
    if type(schema_version) is not int or schema_version != 2 or kind not in FREE_CONDITION_KINDS:
        raise ValueError('Conditional zero-mana prices require a closed version2 condition')
    if kind == 'land_pair':
        if type(own_land) is not str or own_land not in BASIC_TYPES or type(opponent_land) is not str or opponent_land not in BASIC_TYPES:
            raise ValueError('Conditional zero-mana land prices require two canonical basic land types')
    elif own_land is not None or opponent_land is not None:
        raise ValueError('Controlled commander condition cannot carry land fields')
