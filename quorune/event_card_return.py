from __future__ import annotations

"""A departed public card identified by its sealed zone-change occurrence."""

from typing import Any, Mapping

from .semantic_runtime.intents import ZoneMoveIntent


EVENT_CARD_RETURN_CAPABILITY = 'zone.return.fixed_event_card'
EVENT_CARD_RETURN_MECHANIC = 'fixed-event-card-return'
EVENT_BATTLEFIELD_RETURN_CAPABILITY = 'zone.return.fixed_event_battlefield'
EVENT_BATTLEFIELD_RETURN_MECHANIC = 'fixed-event-battlefield-return'
EVENT_LIBRARY_MOVE_CAPABILITY = "zone.return.fixed_event_library"
EVENT_LIBRARY_COMPOSITION_CAPABILITY = "zone.return.fixed_event_library_components"
EVENT_LIBRARY_MOVE_VERSION = "event_library_move_version"
EVENT_LIBRARY_COMPONENTS = "event_library_components"


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


def event_library_move_effect(*, position: str | int, shuffle: bool, version: int = 2) -> dict[str, Any]:
    if (type(position) not in {str, int} or position not in {"top", "bottom", 2, 3, 4, 5}
        or type(shuffle) is not bool or shuffle and position != "top"):
        raise ValueError("Event library movement requires one fixed position or shuffle")
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Event library movement requires a supported instruction version")
    effect = {
        "op": "move_if_in_zone", EVENT_LIBRARY_MOVE_VERSION: version, "card": "$context.card",
        "from": "$context.to", "expected_zone_change_counter": "$context.card_zone_change_counter",
        "owner": "$context.owner", "destination": "library", "position": position, "shuffle": shuffle,
    }
    if version == 2:
        effect.update(composition="$context.composition", components="$context." + EVENT_LIBRARY_COMPONENTS)
    return effect


def is_event_library_move(effect: Mapping[str, Any]) -> bool:
    return EVENT_LIBRARY_MOVE_VERSION in effect or (
        effect.get("op") == "move_if_in_zone" and effect.get("destination") == "library"
        and (effect.get("from") == "$context.to"
             or effect.get("expected_zone_change_counter") == "$context.card_zone_change_counter")
    )


def validate_event_library_move(effect: Mapping[str, Any], *, resolved: bool = False) -> None:
    version = effect.get(EVENT_LIBRARY_MOVE_VERSION)
    expected = event_library_move_effect(position=effect.get("position"), shuffle=effect.get("shuffle"), version=version)
    if not resolved:
        if dict(effect) != expected:
            raise ValueError("Event library movement requires the original departure roles")
        return
    identities = {"card", "from", "expected_zone_change_counter", "owner"}
    if version == 2:
        if not {"composition", "components"} <= effect.keys():
            raise ValueError("Event library movement lost its composition fields")
        identities.update(("composition", "components"))
        _validate_library_components(effect["composition"], effect["components"], effect.get("owner"))
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


def _validate_library_components(composition, components, owner) -> None:
    if composition is None and components is None:
        return
    from .composition_departure import CompositionZoneFact

    group = CompositionZoneFact.from_dict(composition)
    if (not group.permanent_departure or not isinstance(components, (list, tuple))
            or len(components) != group.member_count):
        raise ValueError("Event library movement requires every original physical departure")
    fields = {"object_id", "reference", "logical_object_id", "zone", "owner", "physical_card"}
    for row in components:
        if (not isinstance(row, Mapping) or set(row) != fields
                or any(type(row[key]) is not str or not row[key] for key in fields - {"physical_card"})
                or row["owner"] != owner or row["zone"] not in {"graveyard", "exile", "command", "outside"}
                or type(row["physical_card"]) is not bool):
            raise ValueError("Event library movement has a malformed physical departure")
    if len({row["object_id"] for row in components}) != len(components):
        raise ValueError("Event library movement contains a duplicate physical departure")


def event_library_departure_context(host, context, occurrence, parts):
    """Seal complete post-move identities before the originating trigger is put on the stack."""
    from .rules.event_subscriptions import event_has_capability_subscriber

    if not event_has_capability_subscriber(host, "permanent.leave", EVENT_LIBRARY_COMPOSITION_CAPABILITY):
        return context
    group = occurrence.composition
    if group is None or not group.permanent_departure or len(parts) != group.member_count or any(
        part.composition is None or not part.composition.physical_arrival or part.composition.parent != group.parent
        for part in parts
    ):
        raise ValueError("Event library movement requires its committed composition departure")
    components = [
        {"object_id": part.object_id, "reference": part.card_ref, "logical_object_id": part.current_logical_object_id,
         "zone": part.destination, "owner": part.owner, "physical_card": part.card_object and not part.token}
        for part in parts
    ]
    _validate_library_components(group.to_dict(), components, occurrence.owner)
    return {**dict(context), EVENT_LIBRARY_COMPONENTS: components}


def available_event_library_components(host, effect):
    components = effect.get("components")
    if components is None:
        return None
    return tuple(
        card for member in components
        if member["physical_card"] and member["zone"] in {"graveyard", "exile"}
        and (card := host.state.cards.get(member["object_id"])) is not None
        and card.ref == member["reference"] and card.logical_object_id == member["logical_object_id"]
        and card.zone == member["zone"] and card.owner == member["owner"]
        and card.is_card_object and not card.is_token
    )


def event_library_move_capabilities(effect) -> tuple[str, ...]:
    from .library_order_model import GRAVEYARD_LIBRARY_ORDER_CAPABILITY

    if effect[EVENT_LIBRARY_MOVE_VERSION] == 1:
        return (EVENT_LIBRARY_MOVE_CAPABILITY,)
    required = (EVENT_LIBRARY_MOVE_CAPABILITY, EVENT_LIBRARY_COMPOSITION_CAPABILITY)
    return required if effect["shuffle"] else (*required, GRAVEYARD_LIBRARY_ORDER_CAPABILITY)


def prepare_event_library_order(host, effect):
    if effect.get(EVENT_LIBRARY_MOVE_VERSION) != 2 or effect.get("components") is None or effect.get("shuffle"):
        return effect
    from .errors import GameRuleError
    from .library_order_model import GRAVEYARD_LIBRARY_ORDER_OPERATION

    try:
        validate_event_library_move(effect, resolved=True)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    owner = effect["owner"]
    cards = available_event_library_components(host, effect)
    if owner not in host.active_seats or len(cards) < 2:
        return effect
    return {
        "op": GRAVEYARD_LIBRARY_ORDER_OPERATION, "scope": "event_departure", "player": owner,
        "cards": [card.ref for card in cards], "position": effect["position"],
        "incarnations": {card.ref: card.logical_object_id for card in cards},
    }


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
    components = available_event_library_components(host, resolved)
    if components is not None:
        return not components
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
    return (*event_library_move_capabilities(instruction), *scry,
            *(("effect.choice.optional_fixed",) if optional else ()))


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
