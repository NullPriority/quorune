from __future__ import annotations

"""Pure counter-event permission application; never a quantity replacement."""

from dataclasses import replace
from .counter_permission import (
    ALLOW_ALL_COUNTERS, CounterPermissionError, CounterPermissionSnapshot, CounterPlacementFact,
)
from .replacement.immutable import thaw_value


COUNTER_PERMISSIONS_FIELD = '_counter_permissions'
COUNTER_PROHIBITED_FIELD = '_counter_prohibited'


def counter_event_fact(event, *, amount=None):
    payload = event.payload
    kind = payload.get('target_kind')
    if event.kind != 'counter.place' or type(kind) is not str or kind not in {'player', 'permanent', 'card'}:
        raise CounterPermissionError('Counter permissions require a represented placement event')
    value = payload.get('amount') if amount is None else amount
    if kind == 'player':
        if event.affected_player is None or event.affected_object is not None:
            raise CounterPermissionError('Player placement lost its affected subject')
        return CounterPlacementFact('player', event.affected_player, payload.get('counter_name'), value)
    if event.affected_object is None or event.affected_player is not None:
        raise CounterPermissionError('Permanent placement lost its affected subject')
    types = payload.get('target_types', ())
    if not isinstance(types, (list, tuple)):
        raise CounterPermissionError('Counter placement types must be a fixed sequence')
    controller = payload.get('target_controller')
    if controller is None:
        controller = event.affected_object.controller or event.affected_object.owner
    return CounterPlacementFact('permanent', event.affected_object.object_id,
        payload.get('counter_name'), value,
        controller=controller, zone=payload.get('target_zone'), types=tuple(types))


def apply_counter_permissions(event, permissions=None, *, excluded_sources=()):
    """Seal a denied event as zero, with no replacement application or choice."""
    if event.kind != 'counter.place':
        return event
    was_denied = event.payload.get(COUNTER_PROHIBITED_FIELD) is True
    if permissions is None:
        stored = event.payload.get(COUNTER_PERMISSIONS_FIELD)
        if stored is None:
            if was_denied:
                raise CounterPermissionError('Counter prohibition lost its permission snapshot')
            return event
        permissions = CounterPermissionSnapshot.from_dict(thaw_value(stored))
    if not isinstance(permissions, CounterPermissionSnapshot):
        raise CounterPermissionError('Counter events require typed permissions')
    if isinstance(excluded_sources, (str, bytes)) or any(type(x) is not str or not x for x in excluded_sources):
        raise CounterPermissionError('Counter permission exclusions require source identities')
    if excluded_sources:
        permissions = CounterPermissionSnapshot(tuple(rule for rule in permissions.rules
            if rule.source_object_id not in excluded_sources))
    if not permissions.rules and not was_denied:
        return event
    requested = event.payload.get('requested_amount')
    if was_denied and (type(requested) is not int or requested < 1):
        raise CounterPermissionError('Prohibited counter placement lost its original amount')
    fact = counter_event_fact(event, amount=requested if was_denied else None)
    denied = not permissions.allows(fact)
    payload = thaw_value(event.payload)
    payload[COUNTER_PERMISSIONS_FIELD] = permissions.to_dict()
    if denied:
        payload['amount'] = 0
        payload[COUNTER_PROHIBITED_FIELD] = True
    elif was_denied:
        if event.applied_effects:
            raise CounterPermissionError('A prohibited counter event unexpectedly acquired replacements')
        payload['amount'] = payload['requested_amount']
        payload.pop(COUNTER_PROHIBITED_FIELD, None)
    return replace(event, payload=payload)


def counter_event_is_prohibited(event):
    return event.kind == 'counter.place' and event.payload.get(COUNTER_PROHIBITED_FIELD) is True


def apply_counter_permissions_tree(event, permissions):
    if not isinstance(permissions, CounterPermissionSnapshot):
        raise CounterPermissionError('Counter event trees require typed permissions')
    if not permissions.rules:
        return event
    children = tuple(apply_counter_permissions_tree(child, permissions) for child in event.children)
    event = replace(event, children=children) if children != event.children else event
    return apply_counter_permissions(event, permissions)


def refresh_entry_counter_permissions(event, payload, children):
    """Apply captured pre-entry sources and the selected form after retargeting."""
    stored = payload.get(COUNTER_PERMISSIONS_FIELD)
    if stored is None:
        return
    permissions = CounterPermissionSnapshot.from_dict(thaw_value(stored))
    excluded = event.entry_scope.reserved_zone_changes if event.entry_scope is not None else ()
    for index, child in enumerate(children):
        if child.kind == 'counter.place' and child.payload.get('follows_zone_destination') is True:
            facts = thaw_value(child.payload)
            facts['target_zone'] = payload.get('destination')
            facts['target_kind'] = 'permanent' if facts['target_zone']=='battlefield' else 'card'
            facts['target_types'] = list(payload.get('object_types', ()))
            facts['target_controller'] = payload.get('destination_controller')
            children[index] = apply_counter_permissions(replace(child,payload=facts),
                permissions, excluded_sources=excluded)
