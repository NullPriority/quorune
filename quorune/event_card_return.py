from __future__ import annotations

"""A departed public card identified by its sealed zone-change occurrence."""

from typing import Any, Mapping

from .semantic_runtime.intents import ZoneMoveIntent


EVENT_CARD_RETURN_CAPABILITY = 'zone.return.fixed_event_card'
EVENT_CARD_RETURN_MECHANIC = 'fixed-event-card-return'


def event_card_return_effect() -> dict[str, Any]:
    return {
        'op': 'return_graveyard_card_to_owner_hand', 'schema_version': 2,
        'card': '$context.card', 'expected_zone_change_counter': '$context.card_zone_change_counter',
    }


def event_card_return_intent(effect: Mapping[str, Any], *, actor: str, reason: str) -> ZoneMoveIntent:
    fields = {'op', 'schema_version', 'card', 'expected_zone_change_counter'}
    if set(effect) not in (fields, fields | {'_replacement_selections'}) or effect.get('op') != 'return_graveyard_card_to_owner_hand' or type(effect.get('schema_version')) is not int or effect['schema_version'] != 2:
        raise ValueError('Event-card return requires a closed version-two instruction')
    counter = effect['expected_zone_change_counter']
    if type(effect['card']) is not str or not effect['card'] or type(counter) is not int or counter < 1:
        raise ValueError('Event-card return requires a departed card and exact incarnation')
    selections = effect.get('_replacement_selections', ())
    if not isinstance(selections, (list, tuple)):
        raise ValueError('Event-card return replacements require a bounded sequence')
    return ZoneMoveIntent(
        actor=actor, object_ref=effect['card'], expected_zones=('graveyard',),
        destination='hand', new_controller=None, reason=reason,
        optional_if_missing=True, expected_zone_change_counter=counter,
        replacement_selections=tuple(selections),
    )
