"""Bind optional draw choices and recipients to closed public occurrences."""
from collections.abc import Mapping
from ..compiler.event_optional_draw_results import (
    EVENT_OPTIONAL_DRAW_FIELD, EVENT_OPTIONAL_DRAW_CAPABILITY, EVENT_OPTIONAL_DRAW_MECHANIC,
)


DAMAGE_DEALER_DRAW_VERSION = 3
DAMAGE_DEALER_LIFE_DRAW_VERSION = 9
DAMAGE_DEALER_LIFE_DRAW_CAPABILITY = "resolution.event_player.damage_dealer_life_paid_draw"
_DAMAGE_DEALER_DRAW_VERSIONS = (DAMAGE_DEALER_DRAW_VERSION, DAMAGE_DEALER_LIFE_DRAW_VERSION)
DAMAGE_DEALER_DRAW_VARIANT = "creature_combat_damage_to_source_opponent"
DAMAGE_DEALER_DRAW_CAPABILITY = "resolution.event_player.damage_dealer_optional_draw"
CAST_OPPONENT_DRAW_VERSION = 4
DEFENDING_CONTROLLER_DRAW_VERSION = 5
TABLE_DRAW_ATTACK_RESTRICTION_VERSION = 6
TEMPTING_DRAW_TOKEN_VERSION = 8
ACCEPTED_TOKEN_CREATORS_FIELD = "accepted_token_creators"
CAST_OPPONENT_DRAW_STAGE_FIELDS = frozenset({"player", "cast_draw_order", "cast_draw_choices"})
TABLE_DRAW_STAGE_FIELDS = frozenset({"player", "table_draw_order", "table_draw_choices"})


def tempting_draw_token_effects(token):
    draw = {"op": "draw", "player": "$controller", "count": 1, "private": True}
    offer = {
        "op": "offer_draw", EVENT_OPTIONAL_DRAW_FIELD: TEMPTING_DRAW_TOKEN_VERSION,
        "cast_participant": "$controller", "count": 1, "private": True, "token": token,
    }
    return draw, token, offer


def _tempting_draw_token_payload_dependencies(effect, *, materialized):
    from ..fixed_token_production import FIXED_DISTRIBUTED_TOKEN_OPERATION, fixed_token_effect_shape_is_closed
    from ..replacement.immutable import thaw_value
    from .token_creation_capability_shapes import distributed_token_creation_payload_dependencies

    fields = {"op", EVENT_OPTIONAL_DRAW_FIELD, "cast_participant", "count", "private", "token"}
    caster = effect.get("cast_participant")
    token = effect.get("token")
    if materialized:
        if (type(caster) is not str or not caster or caster != caster.strip() or caster.startswith("$")):
            raise ValueError("The opponent offer requires the original spell controller")
        if CAST_OPPONENT_DRAW_STAGE_FIELDS <= set(effect):
            fields |= CAST_OPPONENT_DRAW_STAGE_FIELDS
    elif caster != "$controller":
        raise ValueError("The opponent offer requires its original controller reference")
    if (set(effect) != fields or effect["op"] != "offer_draw"
            or type(effect[EVENT_OPTIONAL_DRAW_FIELD]) is not int
            or type(effect["count"]) is not int or effect["count"] != 1 or effect["private"] is not True
            or not isinstance(token, Mapping) or token.get("controller") != caster):
        raise ValueError("The opponent offer requires a closed draw and matching token")
    projected_token = {**thaw_value(token), "controller": "$controller"}
    if (not fixed_token_effect_shape_is_closed(projected_token) or token["quantity"] != 1
            or "tapped" in token or not str(token["characteristics"].get("type_line", "")).startswith("Token Creature — ")):
        raise ValueError("The opponent offer requires one ordinary creature token")
    tokens = distributed_token_creation_payload_dependencies({
        "op": FIXED_DISTRIBUTED_TOKEN_OPERATION, "controller": "$controller",
        "scope": "each_player", "token": projected_token,
    })
    return (EVENT_OPTIONAL_DRAW_CAPABILITY, "zone.draw.library_to_hand", *tokens)


def _tempting_draw_token_node_capabilities(effects, target_schema):
    from ..replacement.immutable import thaw_value
    from ..util import stable_json

    if target_schema is not None or len(effects) != 3:
        return ()
    allowed = _tempting_draw_token_payload_dependencies(effects[2], materialized=False)
    expected = tempting_draw_token_effects(effects[2]["token"])
    return allowed if stable_json(thaw_value(effects)) == stable_json(thaw_value(expected)) else ()


def _tempting_draw_token_program_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(effect.get(EVENT_OPTIONAL_DRAW_FIELD) == TEMPTING_DRAW_TOKEN_VERSION
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack")
            or program.cost_schema is not None or program.handlers or program.event_condition is not None):
        raise ValueError("An opponent offer with repeats requires its original resolving spell")
    owned = _tempting_draw_token_node_capabilities(program.effects, program.target_schema)
    if not owned:
        raise ValueError("The spell must draw and create the same token before its opponent offer")
    return owned


def controller_end_step_condition():
    return {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "end_step"},
    ]}


def table_draw_attack_restriction_effect():
    return {"op": "offer_draw", EVENT_OPTIONAL_DRAW_FIELD: TABLE_DRAW_ATTACK_RESTRICTION_VERSION,
            "protected_player": "$controller", "count": 1, "private": True}


def _table_draw_attack_restriction_payload_dependencies(effect, *, materialized):
    from ..next_turn_attack_restriction_model import ATTACK_RESTRICTION_CAPABILITY

    expected = table_draw_attack_restriction_effect()
    fields = set(effect)
    if materialized:
        player = effect.get("protected_player")
        if type(player) is not str or not player or player != player.strip() or player.startswith("$"):
            raise ValueError("Optional table draws require their materialized trigger controller")
        expected["protected_player"] = player
        if TABLE_DRAW_STAGE_FIELDS <= fields:
            fields -= TABLE_DRAW_STAGE_FIELDS
    if (fields != set(expected) or type(effect.get(EVENT_OPTIONAL_DRAW_FIELD)) is not int
            or type(effect.get("count")) is not int or effect.get("private") is not True
            or any(effect.get(key) != value for key, value in expected.items())):
        raise ValueError("Optional table draws require the closed one-card offer and protected player")
    return (EVENT_OPTIONAL_DRAW_CAPABILITY, "resolution.event_player.fixed_result",
            "zone.draw.library_to_hand", ATTACK_RESTRICTION_CAPABILITY)


def _table_draw_attack_restriction_program_dependencies(program):
    from ..replacement.immutable import FrozenMap
    from .effect_shape_walkers import nested_effects

    if not any(effect.get(EVENT_OPTIONAL_DRAW_FIELD) == TABLE_DRAW_ATTACK_RESTRICTION_VERSION
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("step.begin", "battlefield")
            or program.cost_schema is not None or program.target_schema is not None or program.handlers
            or "one_or_more_event_batch" in program.coverage or len(program.effects) != 1
            or not isinstance(program.event_condition, Mapping)
            or FrozenMap(program.event_condition) != FrozenMap(controller_end_step_condition())):
        raise ValueError("Optional table draws require the original controller's end-step occurrence")
    required = _table_draw_attack_restriction_payload_dependencies(program.effects[0], materialized=False)
    return (*required, "trigger.effect.fixed_event", "trigger.placement.apnap")


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
            or effect[EVENT_OPTIONAL_DRAW_FIELD] not in _DAMAGE_DEALER_DRAW_VERSIONS
            or type(effect.get("count")) is not int or effect["count"] != 1
            or effect.get("private") is not True or effect.get("drawer") != effect.get("player")):
        raise ValueError("Damage dealer draw requires one complete private draw for its chooser")
    controller = effect["player"]
    if materialized:
        if type(controller) is not str or not controller or controller.startswith("$"):
            raise ValueError("Damage dealer draw requires a materialized current or last-known controller")
    elif not is_damage_dealer_controller_reference(controller):
        raise ValueError("Damage dealer draw must use the original damage dealer's controller")
    dependencies = (DAMAGE_DEALER_DRAW_CAPABILITY, EVENT_OPTIONAL_DRAW_CAPABILITY,
                    "resolution.event_player.fixed_result", "zone.draw.library_to_hand")
    if effect[EVENT_OPTIONAL_DRAW_FIELD] == DAMAGE_DEALER_LIFE_DRAW_VERSION:
        return (*dependencies, DAMAGE_DEALER_LIFE_DRAW_CAPABILITY, "life.change.effect")
    return dependencies


def _damage_dealer_draw_program_dependencies(program):
    from .effect_shape_walkers import nested_effects
    from ..replacement.immutable import FrozenMap

    if not any(effect.get(EVENT_OPTIONAL_DRAW_FIELD) in _DAMAGE_DEALER_DRAW_VERSIONS
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
    from .target_entry_draw import target_entry_draw_payload_dependencies, uses_target_entry_draw

    if uses_target_entry_draw(effect):
        return target_entry_draw_payload_dependencies(effect, materialized=materialized)

    if isinstance(effect, Mapping) and effect.get(EVENT_OPTIONAL_DRAW_FIELD) == TEMPTING_DRAW_TOKEN_VERSION:
        return _tempting_draw_token_payload_dependencies(effect, materialized=materialized)
    if isinstance(effect, Mapping) and effect.get(EVENT_OPTIONAL_DRAW_FIELD) == TABLE_DRAW_ATTACK_RESTRICTION_VERSION:
        return _table_draw_attack_restriction_payload_dependencies(effect, materialized=materialized)
    if isinstance(effect, Mapping) and effect.get(EVENT_OPTIONAL_DRAW_FIELD) == DEFENDING_CONTROLLER_DRAW_VERSION:
        return _defending_controller_draw_payload_dependencies(effect, materialized=materialized)
    if isinstance(effect, Mapping) and effect.get(EVENT_OPTIONAL_DRAW_FIELD) == CAST_OPPONENT_DRAW_VERSION:
        return _cast_opponent_draw_payload_dependencies(effect, materialized=materialized)
    if isinstance(effect, Mapping) and effect.get(EVENT_OPTIONAL_DRAW_FIELD) in _DAMAGE_DEALER_DRAW_VERSIONS:
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
    from .target_entry_draw import target_entry_draw_node_capabilities, uses_target_entry_draw

    if any(uses_target_entry_draw(effect) for effect in effects):
        return target_entry_draw_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )

    if (effects and effects[-1].get(EVENT_OPTIONAL_DRAW_FIELD) == TEMPTING_DRAW_TOKEN_VERSION
            and EVENT_OPTIONAL_DRAW_MECHANIC in mechanic_ids):
        try:
            return _tempting_draw_token_node_capabilities(effects, target_schema)
        except (KeyError, TypeError, ValueError):
            return ()
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
    from .effect_shape_walkers import nested_effects
    from ..next_turn_attack_restriction_model import ATTACK_RESTRICTION_OPERATION

    if any(effect.get("op") == ATTACK_RESTRICTION_OPERATION
           for effect in nested_effects((program.effects, program.handlers))):
        raise ValueError("Next-turn attack restrictions must come from the committed optional draw choices")
    from .target_entry_draw import target_entry_draw_program_dependencies

    if entry := target_entry_draw_program_dependencies(program):
        return entry

    from .tempt_draw_token import offer_runtime_dependencies

    if offer := offer_runtime_dependencies(program):
        return offer

    if tempting := _tempting_draw_token_program_dependencies(program):
        return tempting
    if death := death_controller_draw_program_dependencies(program):return death
    if dealer := _damage_dealer_draw_program_dependencies(program):
        return dealer
    if cast := _cast_opponent_draw_program_dependencies(program):
        return cast
    if defender := _defending_controller_draw_program_dependencies(program):
        return defender
    if table := _table_draw_attack_restriction_program_dependencies(program):
        return table
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
    from .target_entry_draw import target_entry_draw_program_dependencies

    try:
        from .tempt_draw_token import offer_runtime_dependencies

        required = (offer_runtime_dependencies(program) or target_entry_draw_program_dependencies(program)
                    or _tempting_draw_token_program_dependencies(program)
                    or death_controller_draw_program_dependencies(program)
                    or _damage_dealer_draw_program_dependencies(program)
                    or _cast_opponent_draw_program_dependencies(program)
                    or _defending_controller_draw_program_dependencies(program)
                    or _table_draw_attack_restriction_program_dependencies(program))
    except (KeyError, TypeError, ValueError):
        return False
    if required:
        return set(required) <= set(program.capability_dependencies)
    deps = event_optional_draw_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(deps) and _event_is_closed(program) and set(deps) <= set(program.capability_dependencies)
