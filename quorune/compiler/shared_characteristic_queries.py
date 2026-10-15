from __future__ import annotations

"""Share fixed battlefield syntax without widening characteristic quantity types."""

from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec


def shared_characteristic_quantity(text: str):
    if text.casefold() in {'creature', 'creatures', 'artifact', 'artifacts', 'land', 'lands', 'permanent', 'permanents'}:
        return None
    from .public_state_queries import fixed_battlefield_query_subject
    parsed = fixed_battlefield_query_subject(text)
    if parsed is None:
        return None
    relations = {'any': CharacteristicQuantityScope.ALL_ZONES,
        'source_controller': CharacteristicQuantityScope.CONTROLLER_ZONE,
        'source_opponents': CharacteristicQuantityScope.OPPONENT_ZONES}
    if parsed[0] not in relations:
        return None
    try:
        return CharacteristicQuantitySpec(scope=relations[parsed[0]], query=parsed[1],
            exclude_source=parsed[2], schema_version=1 if parsed[1].state_predicate is not None else 2)
    except ValueError:
        return None
