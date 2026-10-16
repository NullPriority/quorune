from __future__ import annotations

"""Keep granted programs within their represented value-binding contract."""

from collections.abc import Mapping

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
        not contains_public_query_effect_amount(effects) or contains_public_query_characteristic_amount(effects))
