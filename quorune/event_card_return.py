from __future__ import annotations

"""A departed public card identified by its sealed zone-change occurrence."""

from typing import Any, Mapping

from .semantic_runtime.intents import ZoneMoveIntent


EVENT_CARD_RETURN_CAPABILITY = 'zone.return.fixed_event_card'
EVENT_CARD_RETURN_MECHANIC = 'fixed-event-card-return'
EVENT_BATTLEFIELD_RETURN_CAPABILITY = 'zone.return.fixed_event_battlefield'
EVENT_BATTLEFIELD_RETURN_MECHANIC = 'fixed-event-battlefield-return'
EVENT_LIBRARY_MOVE_CAPABILITY = "zone.return.fixed_event_library"
EVENT_LIBRARY_MOVE_VERSION = "event_library_move_version"


def event_library_departure_condition(scope: str) -> dict[str, Any]:
    graveyard = {"field": "to", "op": "eq", "value": "graveyard"}
    conditions = [{"field": "card", "op": "eq", "value": "$source.ref"}]
    if scope == "graveyard_or_exile":
        conditions.append({"any": [graveyard, {"field": "to", "op": "eq", "value": "exile"}]})
    elif scope in {"graveyard", "own_graveyard"}:
        conditions.append(graveyard)
        if scope == "own_graveyard":
            conditions.append({"field": "owner", "op": "eq", "value": "$source.controller"})
    else:
        raise ValueError("Event library moves require a closed departure scope")
    return {"all": conditions}


def event_library_move_effect(*, position: str | int, shuffle: bool) -> dict[str, Any]:
    if (type(position) not in {str, int} or position not in {"top", "bottom", 2, 3, 4, 5}
        or type(shuffle) is not bool or shuffle and position != "top"):
        raise ValueError("Event library movement requires one fixed position or shuffle")
    return {
        "op": "move_if_in_zone", EVENT_LIBRARY_MOVE_VERSION: 1, "card": "$context.card",
        "from": "$context.to", "expected_zone_change_counter": "$context.card_zone_change_counter",
        "owner": "$context.owner", "destination": "library", "position": position, "shuffle": shuffle,
    }


def is_event_library_move(effect: Mapping[str, Any]) -> bool:
    return EVENT_LIBRARY_MOVE_VERSION in effect or (
        effect.get("op") == "move_if_in_zone" and effect.get("destination") == "library"
        and (effect.get("from") == "$context.to"
             or effect.get("expected_zone_change_counter") == "$context.card_zone_change_counter")
    )


def validate_event_library_move(effect: Mapping[str, Any], *, resolved: bool = False) -> None:
    expected = event_library_move_effect(position=effect.get("position"), shuffle=effect.get("shuffle"))
    if type(effect.get(EVENT_LIBRARY_MOVE_VERSION)) is not int or effect[EVENT_LIBRARY_MOVE_VERSION] != 1:
        raise ValueError("Event library moves require their closed versioned instruction")
    if not resolved:
        if dict(effect) != expected:
            raise ValueError("Event library movement requires the original departure roles")
        return
    identities = {"card", "from", "expected_zone_change_counter", "owner"}
    extras = {"_replacement_selections", "_runtime_source"}
    if ({key: value for key, value in effect.items() if key not in identities | extras}
        != {key: value for key, value in expected.items() if key not in identities}):
        raise ValueError("Resolved event library movement has unexpected fields")
    if (
        effect.get("from") not in {"graveyard", "exile"}
        or type(effect.get("expected_zone_change_counter")) is not int or effect["expected_zone_change_counter"] < 1
        or any(type(effect.get(key)) is not str or not effect[key] or effect[key].startswith("$") for key in ("card", "owner"))
    ):
        raise ValueError("Resolved event library movement requires one public departure incarnation and owner")


def unavailable_optional_event_library_move(host, raw_effect, effect) -> bool:
    children = raw_effect.get("effects")
    if not isinstance(children, (list, tuple)) or len(children) != 1 or not isinstance(children[0], Mapping):
        return False
    child, = children
    if not is_event_library_move(child):
        return False
    validate_event_library_move(child)
    resolved, = effect["effects"]
    validate_event_library_move(resolved, resolved=True)
    if resolved["owner"] not in host.active_seats:
        return True
    if resolved["shuffle"]:
        return False
    return not any(
        card.ref == resolved["card"] and card.owner == resolved["owner"] and card.zone == resolved["from"]
        and card.zone_change_counter == resolved["expected_zone_change_counter"]
        for card in host.state.cards.values()
    )


def event_library_move_runtime_dependencies(program) -> tuple[str, ...]:
    def uses_move(value):
        if isinstance(value, Mapping):
            return is_event_library_move(value) or any(uses_move(child) for child in value.values())
        return isinstance(value, (list, tuple)) and any(uses_move(child) for child in value)

    if not uses_move(program.effects):
        return ()
    from .public_zone_moves import FIXED_OWNER_ZONE_MOVE_MECHANIC

    if (
        program.event != "permanent.leave" or program.active_zone != "battlefield"
        or program.target_schema is not None or FIXED_OWNER_ZONE_MOVE_MECHANIC not in program.coverage
        or program.event_condition not in tuple(event_library_departure_condition(scope)
            for scope in ("graveyard", "graveyard_or_exile", "own_graveyard"))
        or len(program.effects) not in {1, 2}
    ):
        raise ValueError("Event library moves require the original battlefield departure")
    instruction = program.effects[0]
    optional = instruction.get("op") == "offer_optional_effect"
    if optional:
        children = instruction.get("effects")
        if (len(program.effects) != 1 or set(instruction) != {"op", "player", "effects"}
            or instruction["player"] != "$controller" or not isinstance(children, (list, tuple))
            or len(children) != 1 or not isinstance(children[0], Mapping)):
            raise ValueError("An optional event library move requires one controller instruction")
        instruction, = children
    scry = ()
    if len(program.effects) == 2:
        from .rules.node_capability_shapes import fixed_scry_node_capabilities

        scry = fixed_scry_node_capabilities(effects=program.effects[1:], target_schema=None, mechanic_ids=program.coverage)
        if not scry or "closed-effect-program" not in program.coverage:
            raise ValueError("An event library move can precede one independently closed scry")
    validate_event_library_move(instruction)
    return (EVENT_LIBRARY_MOVE_CAPABILITY, *scry, *(("effect.choice.optional_fixed",) if optional else ()))


def event_battlefield_return_effect(
    *, tapped: bool, entry_counter: str | None = None, controller: str = '$context.owner',
) -> dict[str, Any]:
    if (type(tapped) is not bool or entry_counter not in (None, '+1/+1')
            or type(controller) is not str or controller not in ('$context.owner', '$controller')):
        raise ValueError('Event battlefield return has unsupported entry instructions')
    return {'op': 'reanimate', 'schema_version': 2, 'card': '$context.card',
        'expected_zone_change_counter': '$context.card_zone_change_counter',
        'controller': controller, 'tapped': tapped, 'entry_counter': entry_counter,
        'source': '$stack'}


def event_battlefield_return_intent(effect: Mapping[str, Any], *, actor: str, reason: str) -> ZoneMoveIntent:
    fields = {'op', 'schema_version', 'card', 'expected_zone_change_counter', 'controller',
        'tapped', 'entry_counter', 'source'}
    if (set(effect) not in (fields, fields | {'_replacement_selections'})
            or effect.get('op') != 'reanimate' or type(effect.get('schema_version')) is not int
            or effect['schema_version'] != 2):
        raise ValueError('Event battlefield return requires a closed version-two instruction')
    incarnation = effect['expected_zone_change_counter']
    if (type(incarnation) is not int or incarnation < 1
            or any(type(effect[k]) is not str or not effect[k] for k in ('card', 'controller', 'source'))
            or type(effect['tapped']) is not bool or effect['entry_counter'] not in {None, '+1/+1'}):
        raise ValueError('Event battlefield return identity and entry facts are malformed')
    selections = effect.get('_replacement_selections', ())
    if not isinstance(selections, (list, tuple)):
        raise ValueError('Event battlefield return replacements require a sequence')
    from .entry_counter_model import EffectEntryCounter
    counters = (() if effect['entry_counter'] is None else
        (EffectEntryCounter(effect['entry_counter'], 1, effect['controller'], effect['source'], '122.6a'),))
    return ZoneMoveIntent(actor=actor, object_ref=effect['card'], expected_zones=('graveyard',),
        destination='battlefield', reason=reason, new_controller=effect['controller'],
        tapped_policy='tapped' if effect['tapped'] else 'untapped', optional_if_missing=True,
        expected_zone_change_counter=incarnation, effect_entry_counters=counters,
        replacement_selections=tuple(selections))


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
