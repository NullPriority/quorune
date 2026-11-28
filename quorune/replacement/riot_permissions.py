from __future__ import annotations

"""The native Riot haste branch when a granted entry cannot receive a counter."""

from dataclasses import replace

from ..counter_permission_events import (
    COUNTER_PERMISSIONS_FIELD, counter_event_is_prohibited, refresh_entry_counter_permissions,
)
from ..riot import RIOT_ENTRY_HANDLER_ID, RIOT_GRANTED_COMPONENT_PREFIX
from .applicability import condition_matches
from .operations import CreateAffectedObjectCounter


def granted_riot_permission_effects(event, effects):
    if (event.kind != "zone.change" or event.payload.get("destination") != "battlefield"
            or COUNTER_PERMISSIONS_FIELD not in event.payload):
        return effects
    scoped = any(
        _is_riot_choice(effect)
        and f":{RIOT_GRANTED_COMPONENT_PREFIX}" in effect.effect_id
        and condition_matches(effect.conditions, event)
        for effect in effects
    )
    if not scoped:
        return effects
    return tuple(_permitted_riot_branch(event, effect) for effect in effects)


def _is_riot_choice(effect):
    return RIOT_ENTRY_HANDLER_ID in effect.effect_id.split(":")


def _permitted_riot_branch(event, effect):
    if (not _is_riot_choice(effect)
            or not effect.optional or len(effect.operations) != 1
            or not isinstance(effect.operations[0], CreateAffectedObjectCounter)
            or not condition_matches(effect.conditions, event)):
        return effect
    from .application import _create_affected_object_counter_event

    children = []
    _create_affected_object_counter_event(event, event.payload, children, effect.operations[0],
                                          effect_id=effect.effect_id)
    refresh_entry_counter_permissions(event, event.payload, children)
    if not counter_event_is_prohibited(children[0]):
        return effect
    return replace(
        effect, operations=effect.decline_operations, decline_operations=(), optional=False,
        label=f'{event.payload["object_ref"]}: gain haste because a Riot counter cannot be placed',
    )
