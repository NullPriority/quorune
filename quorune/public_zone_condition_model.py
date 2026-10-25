from __future__ import annotations

from collections.abc import Mapping


PUBLIC_ZONE_CONDITION_CAPABILITY = 'state_query.public_zone.condition'
PUBLIC_ZONE_FACTS = frozenset({
    'opponent_hand_empty', 'opponent_owns_exiled_card', 'any_library_count_at_most',
})


def validate_public_zone_fact(fact: str, *, amount, parameter, schema_version) -> None:
    if fact not in PUBLIC_ZONE_FACTS:
        return
    if type(schema_version) is not int or schema_version != 4 or type(amount) is not int or amount != 1:
        raise ValueError('Public zone facts require one boolean threshold in schema4')
    if fact == 'any_library_count_at_most':
        if type(parameter) is not str or len(parameter) > 10 or not parameter.isascii() or not parameter.isdecimal() or str(int(parameter)) != parameter or not 0 <= int(parameter) <= 2147483647:
            raise ValueError('Library threshold requires a canonical nonnegative32bit count')
    elif parameter is not None:
        raise ValueError('This public zone fact does not accept a parameter')


def public_zone_fact_value(state, source_controller: str, fact: str, parameter: str | None):
    """Return public count/owner verdicts without inspecting card identities."""
    if fact not in PUBLIC_ZONE_FACTS:
        return None
    seats = tuple(seat for seat in state.turn_order if state.players[seat].in_game)
    opponents = frozenset(seat for seat in seats if seat != source_controller)
    if fact == 'opponent_hand_empty':
        return int(any(not state.players[seat].zones['hand'] for seat in opponents))
    if fact == 'opponent_owns_exiled_card':
        return int(any(card.zone == 'exile' and card.owner in opponents and not card.is_token
            for card in state.cards.values()))
    return int(any(len(state.players[seat].zones['library']) <= int(parameter) for seat in seats))


def public_zone_condition_capabilities(value):
    if isinstance(value, Mapping):
        own = set()
        if value.get('fact') in PUBLIC_ZONE_FACTS:
            from .continuous_conditions import FixedPublicStateConditionSpec
            FixedPublicStateConditionSpec.from_dict(value)
            own.add(PUBLIC_ZONE_CONDITION_CAPABILITY)
        return tuple(sorted({*own, *(cap for child in value.values() for cap in public_zone_condition_capabilities(child))}))
    if isinstance(value, (tuple, list)):
        return tuple(sorted({cap for child in value for cap in public_zone_condition_capabilities(child)}))
    return ()
