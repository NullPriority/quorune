from __future__ import annotations

"""Reuse the closed public query grammar only at an applied instruction."""

import re
from ..live_battlefield_quantity_model import LiveBattlefieldQuantitySpec
from ..object_predicate import ObjectQuerySpec
from ..target_numeric import TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison


_CREATURE_NUMERIC = re.compile(
    r'(?P<other>other )?creatures? you control with (?P<stat>power|toughness) '
    r'(?P<value>[0-9]+) or (?P<bound>greater|less)', re.I,
)
_OPPOSING_CREATURE_COLOR = re.compile(
    r'(?P<color>white|blue|black|red|green) creatures? your opponents control', re.I,
)


def _fixed_numeric_creature_query(text: str) -> tuple[str, ObjectQuerySpec, bool] | None:
    match = _CREATURE_NUMERIC.fullmatch(text)
    if match is None:
        return None
    comparison = (TargetNumericComparison.AT_LEAST if match['bound'].casefold() == 'greater'
                  else TargetNumericComparison.AT_MOST)
    numeric = TargetNumericCharacteristicSpec(
        TargetNumericCharacteristic(match['stat'].casefold()), comparison, int(match['value']),
    )
    return ('source_controller', ObjectQuerySpec(zones=('battlefield',), types_all=('creature',),
                                                numeric_characteristic=numeric), bool(match['other']))


def resolution_quantity(value: str, *, source_name: str, complete_value: bool = False):
    from .query_characteristic_templates import query_characteristic_quantity, query_characteristic_value
    quantity = (query_characteristic_value(value, source_name=source_name) if complete_value
        else query_characteristic_quantity(value, source_name=source_name, definition_extensions=True))
    if quantity is not None:
        from ..query_effect_amount_model import PublicQueryAmountSpec
        try:
            PublicQueryAmountSpec(quantity)
            return quantity
        except ValueError:
            pass
    text = value.strip().rstrip('.')
    if complete_value:
        match = re.fullmatch(r'(?:the )?number of (?P<objects>.+)', text, re.I)
        if match is None:
            return None
        text = match['objects']
    from .public_state_queries import fixed_battlefield_query_subject
    if text.casefold() in {'creature', 'creatures', 'artifact', 'artifacts', 'land', 'lands', 'permanent', 'permanents'}:
        return None
    parsed = fixed_battlefield_query_subject(text) or _fixed_numeric_creature_query(text)
    if parsed is None and (color := _OPPOSING_CREATURE_COLOR.fullmatch(text)) is not None:
        controlled = fixed_battlefield_query_subject(f"{color['color']} creatures you control")
        if controlled is not None:
            parsed = ('source_opponents', controlled[1], False)
    if parsed is None:
        return None
    try:
        return LiveBattlefieldQuantitySpec(parsed[0], parsed[1], parsed[2])
    except ValueError:
        try:
            return LiveBattlefieldQuantitySpec(parsed[0], parsed[1], parsed[2], schema_version=2)
        except ValueError:
            return None
