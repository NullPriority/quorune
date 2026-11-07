from __future__ import annotations

"""Keep granted programs within their represented value-binding contract."""

from collections.abc import Mapping

from ..live_battlefield_quantity_model import LiveBattlefieldQuantitySpec
from ..query_effect_amount_model import PublicQueryAmountSpec
from .public_query_effect_amounts import contains_public_query_characteristic_amount, contains_public_query_effect_amount


def _contains_unbound_x(value):
    if isinstance(value, Mapping):
        return any(_contains_unbound_x(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_unbound_x(child) for child in value)
    return value == '$x'


def granted_effect_values_are_closed(effects):
    # The fixed grant descriptor carries no independent X declaration/binding.
    return not _contains_unbound_x(effects) and (
        not contains_public_query_effect_amount(effects)
        or contains_public_query_characteristic_amount(effects)
        or _independent_qualified_draw(effects))


def _independent_qualified_draw(effects) -> bool:
    if not isinstance(effects, (list, tuple)) or len(effects) != 1:
        return False
    effect = effects[0]
    if (not isinstance(effect, Mapping) or set(effect) != {'op', 'player', 'count', 'private'}
            or effect['op'] != 'draw' or effect['player'] != '$controller' or effect['private'] is not True):
        return False
    try:
        amount = PublicQueryAmountSpec.from_dict(effect['count'])
    except ValueError:
        return False
    return (amount.schema_version == 1 and amount.coefficient == 1
            and isinstance(amount.quantity, LiveBattlefieldQuantitySpec) and amount.quantity.schema_version == 2)
