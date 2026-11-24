"""Bind optional draw choices and recipients to closed public occurrences."""
from collections.abc import Mapping
from ..compiler.event_optional_draw_results import (
    EVENT_OPTIONAL_DRAW_FIELD, EVENT_OPTIONAL_DRAW_CAPABILITY, EVENT_OPTIONAL_DRAW_MECHANIC,
)


DAMAGE_DEALER_DRAW_VERSION = 3
DAMAGE_DEALER_DRAW_VARIANT = "creature_combat_damage_to_source_opponent"
DAMAGE_DEALER_DRAW_CAPABILITY = "resolution.event_player.damage_dealer_optional_draw"
CAST_OPPONENT_DRAW_VERSION = 4
DEFENDING_CONTROLLER_DRAW_VERSION = 5
CAST_OPPONENT_DRAW_STAGE_FIELDS = frozenset({"player", "cast_draw_order", "cast_draw_choices"})


def cast_opponent_optional_draw_effect():
    return {"op": "offer_draw", EVENT_OPTIONAL_DRAW_FIELD: CAST_OPPONENT_DRAW_VERSION,
            "cast_participant": "$context.player", "count": 1, "private": True}


def _cast_opponent_draw_payload_dependencies(effect, *, materialized):
    expected = cast_opponent_optional_draw_effect()
    fields = set(effect)
    if materialized:
        caster = effect.get("cast_participant")
        if type(caster) is not str or not caster or caster != caster.strip() or caster.startswith("$"):
            raise ValueError("Optional cast opponent draws require their materialized caster")
        expected["cast_participant"] = caster
        if CAST_OPPONENT_DRAW_STAGE_FIELDS <= fields:
            fields -= CAST_OPPONENT_DRAW_STAGE_FIELDS
    if (fields != set(expected)
            or type(effect.get(EVENT_OPTIONAL_DRAW_FIELD)) is not int
            or type(effect.get("count")) is not int
            or effect.get("private") is not True
            or any(effect.get(key) != value for key, value in expected.items())):
        raise ValueError("Optional cast opponent draws require the closed one-card offer")
    return (EVENT_OPTIONAL_DRAW_CAPABILITY, "resolution.event_player.fixed_result",
            "zone.draw.library_to_hand", "trigger.event.normalized_spell_cast")


def _cast_opponent_draw_program_dependencies(program):
    from ..replacement.immutable import FrozenMap
    from .effect_shape_walkers import nested_effects
    from .event_outcome_shapes import noncreature_cast_condition

    if not any(effect.get(EVENT_OPTIONAL_DRAW_FIELD) == CAST_OPPONENT_DRAW_VERSION
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("spell.cast", "battlefield")
            or program.cost_schema is not None or program.target_schema is not None or program.handlers
            or "one_or_more_event_batch" in program.coverage
            or not isinstance(program.event_condition, Mapping)
            or FrozenMap(program.event_condition) != FrozenMap(noncreature_cast_condition())
            or len(program.effects) != 1):
        raise ValueError("Optional cast opponent draws require one unqualified noncreature spell-cast occurrence")
    required = _cast_opponent_draw_payload_dependencies(program.effects[0], materialized=False)
    return (*required, "trigger.effect.fixed_event", "trigger.placement.apnap")


def defending_controller_draw_effect():
    return {"op": "offer_draw", EVENT_OPTIONAL_DRAW_FIELD: DEFENDING_CONTROLLER_DRAW_VERSION,
            "player": "$context.defending_player", "drawer": "$controller", "count": 1, "private": True}


def _defending_controller_draw_payload_dependencies(effect, *, materialized):
    from ..combat_removal import COMBAT_REMOVAL_CAPABILITY

    expected = defending_controller_draw_effect()
    if materialized:
        for key in ("player", "drawer"):
            seat = effect.get(key)
            if type(seat) is not str or not seat or seat != seat.strip() or seat.startswith("$"):
                raise ValueError("Defending-player draw requires its materialized chooser and drawer")
            expected[key] = seat
    if (effect != expected or type(effect.get(EVENT_OPTIONAL_DRAW_FIELD)) is not int
            or type(effect.get("count")) is not int or effect.get("private") is not True):
        raise ValueError("Defending-player draw requires its exact private draw and combat result")
    return (EVENT_OPTIONAL_DRAW_CAPABILITY, "resolution.event_player.fixed_result",
            "zone.draw.library_to_hand", "permanent.untap.effect", COMBAT_REMOVAL_CAPABILITY)


def _defending_controller_draw_program_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(effect.get(EVENT_OPTIONAL_DRAW_FIELD) == DEFENDING_CONTROLLER_DRAW_VERSION
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or program.cost_schema is not None or program.target_schema is not None or program.handlers
            or "one_or_more_event_batch" in program.coverage or len(program.effects) != 1
            or program.event_condition != {"field": "card", "op": "eq", "value": "$source.ref"}):
        raise ValueError("Defending-player draw requires the exact original attack occurrence")
    required = _defending_controller_draw_payload_dependencies(program.effects[0], materialized=False)
    return (*required, "trigger.effect.fixed_event", "trigger.event.normalized_self_attack", "trigger.placement.apnap")


def damage_dealer_draw_condition():
    return {"all": [
        {"field": "source_types", "op": "contains_any", "value": ["creature"]},
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
        {"field": "target", "op": "ne", "value": "$source.controller"},
    ]}


def _damage_dealer_draw_payload_dependencies(effect, *, materialized):
    from ..target_controller_information import is_damage_dealer_controller_reference

    if (set(effect) != {"op", "player", "drawer", "count", "private", EVENT_OPTIONAL_DRAW_FIELD}
            or effect.get("op") != "offer_draw" or type(effect.get(EVENT_OPTIONAL_DRAW_FIELD)) is not int
            or effect[EVENT_OPTIONAL_DRAW_FIELD] != DAMAGE_DEALER_DRAW_VERSION
            or type(effect.get("count")) is not int or effect["count"] != 1
            or effect.get("private") is not True or effect.get("drawer") != effect.get("player")):
        raise ValueError("Damage dealer draw requires one complete private draw for its chooser")
    controller = effect["player"]
    if materialized:
        if type(controller) is not str or not controller or controller.startswith("$"):
            raise ValueError("Damage dealer draw requires a materialized current or last-known controller")
    elif not is_damage_dealer_controller_reference(controller):
        raise ValueError("Damage dealer draw must use the original damage dealer's controller")
    return (DAMAGE_DEALER_DRAW_CAPABILITY, EVENT_OPTIONAL_DRAW_CAPABILITY,
            "resolution.event_player.fixed_result", "zone.draw.library_to_hand")


def _damage_dealer_draw_program_dependencies(program):
    from .effect_shape_walkers import nested_effects
    from ..replacement.immutable import FrozenMap

    if not any(effect.get(EVENT_OPTIONAL_DRAW_FIELD) == DAMAGE_DEALER_DRAW_VERSION
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("damage.dealt", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or "one_or_more_event_batch" in program.coverage
            or not isinstance(program.event_condition, Mapping)
            or FrozenMap(program.event_condition) != FrozenMap(damage_dealer_draw_condition())
            or len(program.effects) != 1):
        raise ValueError("Damage dealer draw requires the exact creature combat damage to a source opponent occurrence")
    required = _damage_dealer_draw_payload_dependencies(program.effects[0], materialized=False)
    return (*required, "trigger.effect.fixed_event", "trigger.event.normalized_damage",
            "trigger.placement.apnap")


def validate_event_optional_draw_payload(effect, *, materialized=False):
    if isinstance(effect, Mapping) and effect.get(EVENT_OPTIONAL_DRAW_FIELD) == DEFENDING_CONTROLLER_DRAW_VERSION:
        return _defending_controller_draw_payload_dependencies(effect, materialized=materialized)
    if isinstance(effect, Mapping) and effect.get(EVENT_OPTIONAL_DRAW_FIELD) == CAST_OPPONENT_DRAW_VERSION:
        return _cast_opponent_draw_payload_dependencies(effect, materialized=materialized)
    if isinstance(effect, Mapping) and effect.get(EVENT_OPTIONAL_DRAW_FIELD) == DAMAGE_DEALER_DRAW_VERSION:
        return _damage_dealer_draw_payload_dependencies(effect, materialized=materialized)
    if isinstance(effect, Mapping) and effect.get(EVENT_OPTIONAL_DRAW_FIELD) == 2:
        from .event_creature_controller_results import death_controller_draw_payload_dependencies
        return death_controller_draw_payload_dependencies(effect, materialized=materialized)
    if (not isinstance(effect, Mapping)
            or set(effect) != {'op', 'player', 'drawer', 'count', 'private', EVENT_OPTIONAL_DRAW_FIELD}
            or effect.get('op') != 'offer_draw' or type(effect.get(EVENT_OPTIONAL_DRAW_FIELD)) is not int
            or effect[EVENT_OPTIONAL_DRAW_FIELD] != 1 or type(effect.get('count')) is not int
            or effect['count'] < 1 or effect.get('private') is not True):
        raise ValueError('Event optional draw requires the complete version-one private fixed draw')
    player = effect.get('player')
    if player != effect.get('drawer'):
        raise ValueError('The sealed event participant must both choose and draw')
    if materialized:
        if type(player) is not str or not player or player.startswith('$'):
            raise ValueError('The event draw participant must already be materialized')
    elif player != '$context.defending_player':
        raise ValueError('Only the represented defending-player reference is admitted')
    return (EVENT_OPTIONAL_DRAW_CAPABILITY, 'resolution.event_player.fixed_result', 'zone.draw.library_to_hand')


def event_optional_draw_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) == 1 and isinstance(effects[0], Mapping) and effects[0].get(EVENT_OPTIONAL_DRAW_FIELD) == 2:
        from .event_creature_controller_results import death_controller_draw_node_capabilities
        return death_controller_draw_node_capabilities(effects=effects, target_schema=target_schema)
    if (len(effects) != 1 or target_schema is not None
            or EVENT_OPTIONAL_DRAW_MECHANIC not in mechanic_ids):return ()
    try:return validate_event_optional_draw_payload(effects[0])
    except (TypeError, ValueError):return ()


def _event_is_closed(program):
    from .event_player_result_shapes import _source_combat_player_scope
    return (program.event == 'creature.attacks' and program.active_zone == 'battlefield'
            and program.target_schema is None and 'one_or_more_event_batch' not in program.coverage
            and _source_combat_player_scope(program.event_condition))


def event_optional_draw_runtime_dependencies(program):
    from .event_creature_controller_results import death_controller_draw_program_dependencies
    if death := death_controller_draw_program_dependencies(program):return death
    if dealer := _damage_dealer_draw_program_dependencies(program):
        return dealer
    if cast := _cast_opponent_draw_program_dependencies(program):
        return cast
    if defender := _defending_controller_draw_program_dependencies(program):
        return defender
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            if EVENT_OPTIONAL_DRAW_FIELD in value:
                required.update(validate_event_optional_draw_payload(value))
                if not _event_is_closed(program):
                    raise ValueError('The defending-player draw requires its original source attack occurrence')
                required.update(('trigger.effect.fixed_event', 'trigger.event.normalized_self_attack', 'trigger.placement.apnap'))
            for child in value.values():visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))


def is_closed_event_optional_draw_program(program):
    from .event_creature_controller_results import death_controller_draw_program_dependencies
    try:
        required = (death_controller_draw_program_dependencies(program)
                    or _damage_dealer_draw_program_dependencies(program)
                    or _cast_opponent_draw_program_dependencies(program)
                    or _defending_controller_draw_program_dependencies(program))
    except (KeyError, TypeError, ValueError):
        return False
    if required:
        return set(required) <= set(program.capability_dependencies)
    deps = event_optional_draw_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(deps) and _event_is_closed(program) and set(deps) <= set(program.capability_dependencies)
