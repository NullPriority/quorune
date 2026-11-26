from __future__ import annotations

"""A departed public card identified by its sealed zone-change occurrence."""

from typing import Any, Mapping

from .semantic_runtime.intents import ZoneMoveIntent


EVENT_CARD_RETURN_CAPABILITY = 'zone.return.fixed_event_card'
EVENT_CARD_RETURN_MECHANIC = 'fixed-event-card-return'
EVENT_HAND_RETURN_CAPABILITY = "zone.return.fixed_event_departure_owner_hand"
EVENT_HAND_RETURN_VERSION = "event_hand_return_version"
EVENT_HAND_RETURN_MECHANIC = "fixed-event-departure-owner-hand"
EVENT_BATTLEFIELD_RETURN_CAPABILITY = 'zone.return.fixed_event_battlefield'
EVENT_BATTLEFIELD_RETURN_MECHANIC = 'fixed-event-battlefield-return'
EVENT_LIBRARY_MOVE_CAPABILITY = "zone.return.fixed_event_library"
EVENT_LIBRARY_COMPOSITION_CAPABILITY = "zone.return.fixed_event_library_components"
EVENT_LIBRARY_MOVE_VERSION = "event_library_move_version"
EVENT_LIBRARY_COMPONENTS = "event_library_components"
GRAVEYARD_SOURCE_ARRIVAL_EVENT = "object.enter_graveyard"
GRAVEYARD_SOURCE_ARRIVAL_CAPABILITY = "trigger.event.normalized_anywhere_graveyard"
GRAVEYARD_SOURCE_SHUFFLE_TEMPLATE = "fixed-counter-source-graveyard-arrival-shuffle-v1"


def graveyard_source_arrival_condition():
    return {"all": [
        {"field": "card", "op": "eq", "value": "$source.ref"},
        {"field": "card_object_identity", "op": "eq", "value": "$source.logical_object_id"},
        {"field": "owner", "op": "eq", "value": "$source.owner"},
    ]}


def dispatch_graveyard_source_arrival(host, occurrence, card, *, trigger_batch):
    """Observe only the newly arrived source, before state-based token removal."""
    if (
        occurrence.destination != "graveyard" or occurrence.origin == "graveyard"
        or occurrence.composition is not None and not occurrence.composition.physical_arrival
    ):
        return
    from .rules.event_subscriptions import event_has_capability_subscriber

    if not event_has_capability_subscriber(host, GRAVEYARD_SOURCE_ARRIVAL_EVENT, GRAVEYARD_SOURCE_ARRIVAL_CAPABILITY):
        return
    host._dispatch_semantic_event(GRAVEYARD_SOURCE_ARRIVAL_EVENT, {
        "card": occurrence.card_ref,
        "card_object_identity": occurrence.current_logical_object_id,
        "card_zone_change_counter": occurrence.zone_change_counter,
        "owner": occurrence.owner,
        "to": occurrence.destination,
    }, sources=(card,), trigger_batch=trigger_batch)


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

    if not any(event_has_capability_subscriber(host, "permanent.leave", capability)
               for capability in (EVENT_LIBRARY_COMPOSITION_CAPABILITY, EVENT_HAND_RETURN_CAPABILITY)):
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


def _graveyard_source_shuffle_dependencies(program):
    from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from .public_zone_moves import FIXED_OWNER_ZONE_MOVE_MECHANIC
    from .replacement.immutable import FrozenMap

    expected = event_library_move_effect(position="top", shuffle=True, version=1)
    if (
        (program.event, program.active_zone) != (GRAVEYARD_SOURCE_ARRIVAL_EVENT, "graveyard")
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or not {CURRENT_ABILITY_FRAGMENT_COVERAGE, FIXED_OWNER_ZONE_MOVE_MECHANIC} <= set(program.coverage)
        or "one_or_more_event_batch" in program.coverage
        or not isinstance(program.event_condition, Mapping)
        or FrozenMap(program.event_condition) != FrozenMap(graveyard_source_arrival_condition())
        or len(program.effects) != 1 or dict(program.effects[0]) != expected
    ):
        raise ValueError("An arrival shuffle requires the current graveyard source and its sealed owner")
    validate_event_library_move(program.effects[0])
    return (*event_library_move_capabilities(expected), GRAVEYARD_SOURCE_ARRIVAL_CAPABILITY)


def event_library_move_runtime_dependencies(program) -> tuple[str, ...]:
    arrival_template = GRAVEYARD_SOURCE_SHUFFLE_TEMPLATE.replace("fixed-counter-", "fixed-typed-effect-", 1)
    if (program.event == GRAVEYARD_SOURCE_ARRIVAL_EVENT
            or arrival_template in str(program.provenance.get("template_id") or "").split("+")):
        return _graveyard_source_shuffle_dependencies(program)

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


def event_hand_return_effect() -> dict[str, Any]:
    return {
        "op": "move_if_in_zone", EVENT_HAND_RETURN_VERSION: 1,
        "card": "$context.card", "from": "$context.to", "owner": "$context.owner",
        "expected_zone_change_counter": "$context.card_zone_change_counter",
        "destination": "hand", "composition": "$context.composition",
        "components": "$context." + EVENT_LIBRARY_COMPONENTS,
    }


def is_event_hand_return(effect: Mapping[str, Any]) -> bool:
    return EVENT_HAND_RETURN_VERSION in effect or (
        effect.get("op") == "move_if_in_zone" and effect.get("destination") == "hand"
        and ("components" in effect or "composition" in effect)
    )


def event_hand_return_capabilities() -> tuple[str, ...]:
    return (EVENT_HAND_RETURN_CAPABILITY, "trigger.event.normalized_zone_change",
            "trigger.effect.fixed_event", "trigger.placement.apnap",
            "zone.change.destination_replacement", "zone.composition.destination_result")


def validate_event_hand_return(effect: Mapping[str, Any], *, resolved: bool = False) -> None:
    expected = event_hand_return_effect()
    if not resolved:
        if dict(effect) != expected or type(effect[EVENT_HAND_RETURN_VERSION]) is not int:
            raise ValueError("Departure hand returns require the original occurrence roles")
        return
    bindings = {"card", "from", "owner", "expected_zone_change_counter", "composition", "components"}
    extras = {"_replacement_selections", "_runtime_source"}
    if (
        set(effect) - extras != set(expected) or type(effect[EVENT_HAND_RETURN_VERSION]) is not int
        or {key: value for key, value in effect.items() if key not in bindings | extras}
           != {key: value for key, value in expected.items() if key not in bindings}
        or effect["from"] not in {"graveyard", "exile"}
        or type(effect["expected_zone_change_counter"]) is not int or effect["expected_zone_change_counter"] < 1
        or any(type(effect[key]) is not str or not effect[key] or effect[key].startswith("$") for key in ("card", "owner"))
        or not isinstance(effect.get("_replacement_selections", ()), (tuple, list))
    ):
        raise ValueError("Departure hand returns require a sealed public arrival and owner")
    _validate_library_components(effect["composition"], effect["components"], effect["owner"])


def event_hand_return_node_capabilities(*, effects, target_schema, mechanic_ids) -> tuple[str, ...]:
    if (
        target_schema is None and EVENT_HAND_RETURN_MECHANIC in mechanic_ids and len(effects) == 1
        and dict(effects[0]) == event_hand_return_effect() and type(effects[0][EVENT_HAND_RETURN_VERSION]) is int
    ):
        return event_hand_return_capabilities()
    return ()


def event_hand_return_runtime_dependencies(program) -> tuple[str, ...]:
    def uses_return(value):
        if isinstance(value, Mapping):
            return is_event_hand_return(value) or any(uses_return(child) for child in value.values())
        return isinstance(value, (list, tuple)) and any(uses_return(child) for child in value)

    if not uses_return(program.effects):
        return ()
    from .replacement.immutable import FrozenMap

    if (
        (program.event, program.active_zone) != ("permanent.leave", "battlefield")
        or program.cost_schema is not None or program.handlers
        or FrozenMap(program.event_condition or {}) != FrozenMap(event_library_departure_condition("graveyard_or_exile"))
        or not event_hand_return_node_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
    ):
        raise ValueError("Departure hand returns require the source's original graveyard-or-exile occurrence")
    return event_hand_return_capabilities()


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



DEATH_LAND_EXILE_CAPABILITY = "zone.move.fixed_death_source_exile_after_land_type"
DEATH_LAND_EXILE_MECHANIC = "fixed-death-land-type-and-exile"
DEATH_EXILE_THEN_RETURN_CAPABILITY = "zone.move.fixed_death_source_exile_before_graveyard_return"
DEATH_EXILE_THEN_REANIMATE_CAPABILITY = "zone.reanimate.after_source_death_exile"
DEATH_SOURCE_RETURN_TARGET_FIELD = "death_source_return_target_version"
DEATH_EXILE_ARRIVAL_CAPABILITIES = frozenset({
    DEATH_LAND_EXILE_CAPABILITY, DEATH_EXILE_THEN_RETURN_CAPABILITY, DEATH_EXILE_THEN_REANIMATE_CAPABILITY,
})
DEATH_EXILE_ARRIVALS = "death_exile_arrivals"
DEATH_EXILE_VERSION = "death_exile_version"
_DEATH_EXILE_PUBLIC_ZONES = ("graveyard", "exile", "command")


def death_land_exile_condition():
    return {"all": [
        {"field": "card", "op": "eq", "value": "$source.ref"},
        {"field": "card_object_identity", "op": "eq", "value": "$source.logical_object_id"},
    ]}


def death_source_exile_effect():
    return {"op": "move_if_in_zone", DEATH_EXILE_VERSION: 1,
            DEATH_EXILE_ARRIVALS: "$context." + DEATH_EXILE_ARRIVALS}


def has_death_source_exile(effect):
    return DEATH_EXILE_VERSION in effect or DEATH_EXILE_ARRIVALS in effect


def death_source_exile_context(host, context, occurrence, parts):
    from .rules.event_subscriptions import event_has_capability_subscriber

    if not any(event_has_capability_subscriber(host, "creature.dies", capability)
               for capability in sorted(DEATH_EXILE_ARRIVAL_CAPABILITIES)):
        return context
    if occurrence.composition is None:
        arrivals = (occurrence,)
    else:
        group = occurrence.composition
        if not group.permanent_departure or len(parts) != group.member_count or any(
            part.composition is None or not part.composition.physical_arrival
            or part.composition.parent != group.parent for part in parts
        ):
            raise ValueError("Death-source exile requires its complete committed departures")
        arrivals = parts
    rows = [
        {"object_id": part.object_id, "reference": part.card_ref,
         "logical_object_id": part.current_logical_object_id, "zone": part.destination}
        for part in arrivals if part.card_object and not part.token
        and part.destination in _DEATH_EXILE_PUBLIC_ZONES
    ]
    return {**dict(context), DEATH_EXILE_ARRIVALS: rows}


def death_land_exile_node_capabilities(effects, target_schema):
    from .resolution_characteristic_model import fixed_resolution_characteristic_instruction
    from .rules.resolution_characteristic_shapes import fixed_resolution_characteristic_capabilities
    from .target_land_type_model import FIXED_TARGET_LAND_TYPE_MECHANIC, FixedTargetLandTypeSpec

    if (len(effects) != 2 or type(effects[1].get(DEATH_EXILE_VERSION)) is not int
            or effects[1] != death_source_exile_effect()):
        return ()
    try:
        spec, selection = fixed_resolution_characteristic_instruction(effects[0])
    except (KeyError, TypeError, ValueError):
        return ()
    if (not isinstance(spec, FixedTargetLandTypeSpec) or spec.duration != "zone_object"
            or spec.retain_other_land_types or spec.controller_relation != "any"
            or selection != "$target.0"):
        return ()
    required = fixed_resolution_characteristic_capabilities(
        effects=effects[:1], target_schema=target_schema,
        mechanic_ids=(FIXED_TARGET_LAND_TYPE_MECHANIC, "cr-611-continuous-effects", "cr-115-targets"),
    )
    return (*required, DEATH_LAND_EXILE_CAPABILITY) if required else ()


def death_exile_then_return_node_capabilities(effects, target_schema):
    from .replacement.immutable import thaw_value
    from .rules.source_excluding_graveyard_returns import source_excluding_graveyard_return_node_capabilities

    schema = thaw_value(target_schema)
    if (len(effects) != 2 or type(effects[0].get(DEATH_EXILE_VERSION)) is not int
            or effects[0] != death_source_exile_effect()
            or not isinstance(schema, Mapping) or schema.get("types_any") != ["creature"]):
        return ()
    from .rules.graveyard_card_targets import GraveyardCardTargetKind, OwnGraveyardCardTargetSpec
    from .rules.reanimation_capability_shapes import fixed_target_reanimation_node_capabilities

    if schema == OwnGraveyardCardTargetSpec(GraveyardCardTargetKind.CREATURE_CARD).to_target_schema():
        result = dict(effects[1])
        version = result.pop(DEATH_SOURCE_RETURN_TARGET_FIELD, None)
        if type(version) is not int or version != 1:
            return ()
        required = fixed_target_reanimation_node_capabilities(effects=(result,), target_schema=schema,
            mechanic_ids=("fixed-target-reanimation", "cr-115-targets"))
        if required and effects[1].get("controller") == "$controller" and effects[1].get("tapped") is False:
            return (*required, "card.exile.public_graveyard", DEATH_EXILE_THEN_REANIMATE_CAPABILITY)
    base = source_excluding_graveyard_return_node_capabilities(
        effects=effects[1:], target_schema=schema, mechanic_ids=("return-to-owner-hand", "cr-115-targets"),
    )
    return (*base, "card.exile.public_graveyard", DEATH_EXILE_THEN_RETURN_CAPABILITY) if base else ()


def death_land_exile_runtime_dependencies(program):
    from .replacement.immutable import FrozenMap
    from .rules.effect_shape_walkers import nested_effects

    if not any(has_death_source_exile(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if (program.event != "creature.dies" or program.active_zone != "battlefield"
            or FrozenMap(program.event_condition or {}) != FrozenMap(death_land_exile_condition())):
        raise ValueError("Death-source exile needs its exact intrinsic dies occurrence")
    required = (death_land_exile_node_capabilities(program.effects, program.target_schema)
                or death_exile_then_return_node_capabilities(program.effects, program.target_schema))
    if not required:
        raise ValueError("Death-source exile requires its exact ordered target land setting or owned return")
    return required


def current_death_source_return_effect(host, effect):
    """Restrict the ordered second step to the selected graveyard incarnation."""
    if DEATH_SOURCE_RETURN_TARGET_FIELD not in effect:
        return effect
    from .errors import GameRuleError
    from .rules.reanimation_followup import current_linked_subject

    version = effect[DEATH_SOURCE_RETURN_TARGET_FIELD]
    target = effect.get("target_snapshot")
    if (type(version) is not int or version != 1 or not isinstance(target, Mapping)
            or set(target) != {"object_id", "ref", "zone_change_counter"}
            or target["ref"] != effect.get("card") or type(target["zone_change_counter"]) is not int):
        raise GameRuleError("Death-source reanimation requires its bound original graveyard target")
    if current_linked_subject(host, target, zone="graveyard") is None:
        return None
    return {key: value for key, value in effect.items() if key not in {DEATH_SOURCE_RETURN_TARGET_FIELD, "target_snapshot"}}


def apply_death_source_exile(host, effect, *, actor, reason):
    from .errors import GameRuleError
    from .semantic_runtime.intents import MoveObjectsSimultaneouslyIntent

    required = {"op", DEATH_EXILE_VERSION, DEATH_EXILE_ARRIVALS}
    if (not required <= set(effect) <= required | {"_runtime_source", "_replacement_selections"}
            or effect["op"] != "move_if_in_zone"
            or type(effect[DEATH_EXILE_VERSION]) is not int or effect[DEATH_EXILE_VERSION] != 1):
        raise GameRuleError("Death-source exile has a closed resolved instruction")
    arrivals = effect[DEATH_EXILE_ARRIVALS]
    if not isinstance(arrivals, (list, tuple)):
        raise GameRuleError("Death-source exile requires the committed public arrivals")
    objects = set()
    refs = []
    for row in arrivals:
        fields = {"object_id", "reference", "logical_object_id", "zone"}
        if (not isinstance(row, Mapping) or set(row) != fields
                or any(type(row[field]) is not str or not row[field] for field in fields)
                or row["zone"] not in _DEATH_EXILE_PUBLIC_ZONES or row["object_id"] in objects):
            raise GameRuleError("Death-source exile has a malformed committed arrival")
        objects.add(row["object_id"])
        card = host.state.cards.get(row["object_id"])
        if (card is not None and card.ref == row["reference"]
                and card.logical_object_id == row["logical_object_id"] and card.zone == row["zone"]
                and card.is_card_object and not card.is_token):
            refs.append(card.ref)
    if not refs:
        return ()
    return host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
        actor=actor, object_refs=tuple(refs), expected_zones=_DEATH_EXILE_PUBLIC_ZONES,
        destination="exile", reason=reason,
        replacement_selections=tuple(effect.get("_replacement_selections") or ()),
    ))
