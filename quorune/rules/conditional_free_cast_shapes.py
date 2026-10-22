from __future__ import annotations

from collections.abc import Mapping

from ..conditional_free_cast_model import CONDITIONAL_FREE_CAST_CAPABILITY, FREE_CONDITION_KINDS


def conditional_free_cast_capabilities(value):
    if isinstance(value, Mapping):
        own = set()
        if value.get('kind') in FREE_CONDITION_KINDS:
            from ..public_alternative_costs import FixedPublicAlternativeCondition
            condition = FixedPublicAlternativeCondition.from_dict(value)
            own.add(CONDITIONAL_FREE_CAST_CAPABILITY)
            if condition.kind.value == 'controlled_commander':
                own.add('state_query.commander.designation')
        return tuple(sorted({*own, *(cap for child in value.values() for cap in conditional_free_cast_capabilities(child))}))
    if isinstance(value, (tuple, list)):
        return tuple(sorted({cap for child in value for cap in conditional_free_cast_capabilities(child)}))
    return ()
