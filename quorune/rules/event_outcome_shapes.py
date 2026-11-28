"""Payload-derived requirements for exact cast and damage occurrence results."""
from collections.abc import Mapping
from math import isfinite

from ..life_change import LIFE_LOSS_CAPABILITY, LIFE_LOSS_EVENT
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects
from ..source_sacrifice_model import (
    SOURCE_SACRIFICE_FIELD, fixed_source_sacrifice_dependencies, fixed_source_sacrifice_effect,
)

CAST_EVENT_COUNTER_CAPABILITY = 'stack.counter.cast_event_spell'
PLAYER_LOSS_CAPABILITY = 'game.loss.effect'
CAST_TRIGGER_SACRIFICE_COUNTER_CAPABILITY = "resolution.effect.cast_trigger_source_sacrifice_counter"
CAST_EVENT_COUNTER_BIRDS_CAPABILITY = "resolution.effect.cast_event_counter_mana_value_birds"
OPPONENT_LIFE_LOSS_MECHANIC = "opponent-life-loss-result"
CONTROLLER_LIFE_LOSS_DRAW_MECHANIC = "controller-life-loss-draw"
CONTROLLER_LIFE_LOSS_DRAW_VARIANT = "controller_life_loss_draw"
_COUNTER = {'op':'counter_stack_target','stack':'$context.stack'}
_LOSS = {'op':'lose_game','player':'$context.target'}

_ZERO_MANA_FIELD = "mana_spent_total"
_ZERO_MANA_DAMAGE = {"op": "damage", "source": "$source", "target": "$context.player", "amount": 5}
_OPPONENT_CAST = {"field": "controller", "op": "ne", "value": "$source.controller"}
CAST_REVEAL_MANA_VALUE_COUNTER = "cast_reveal_mana_value_counter"
CAST_REVEAL_COUNTER_CAPABILITIES = (
    CAST_EVENT_COUNTER_CAPABILITY, "stack.counter.effect", "trigger.event.normalized_spell_cast",
    "trigger.placement.apnap", "effect.choice.optional_fixed", "library.select.fixed_controller",
)


def cast_reveal_counter_effect(*, player="$controller", stack="$context.stack", mana_value="$context.mana_value"):
    return {
        "op": "offer_optional_effect", "player": player,
        "effects": [{
            "op": "counter_stack_target", "stack": stack,
            CAST_REVEAL_MANA_VALUE_COUNTER: {"schema_version": 1, "mana_value": mana_value},
        }],
    }


def uses_cast_reveal_counter(effect):
    if not isinstance(effect, Mapping) or effect.get("op") != "offer_optional_effect":
        return False
    children = effect.get("effects")
    return isinstance(children, (list, tuple)) and any(
        isinstance(child, Mapping) and CAST_REVEAL_MANA_VALUE_COUNTER in child for child in children
    )


def validate_cast_reveal_counter(effect, *, actor=None):
    if not uses_cast_reveal_counter(effect) or len(effect["effects"]) != 1:
        raise ValueError("The optional reveal must have exactly one original cast result")
    child = effect["effects"][0]
    marker = child[CAST_REVEAL_MANA_VALUE_COUNTER]
    if not isinstance(marker, Mapping) or type(marker.get("schema_version")) is not int:
        raise ValueError("The cast reveal comparison requires a closed versioned value")
    stack, mana_value = child.get("stack"), marker.get("mana_value")
    if actor is None:
        expected = cast_reveal_counter_effect()
    else:
        if (not isinstance(actor, str) or not actor or actor.startswith("$")
                or not isinstance(stack, str) or not stack or stack.startswith("$")
                or type(mana_value) not in (int, float) or mana_value < 0
                or type(mana_value) is float and not isfinite(mana_value)):
            raise ValueError("The reveal comparison requires a real actor and committed spell facts")
        expected = cast_reveal_counter_effect(player=actor, stack=stack, mana_value=mana_value)
    if thaw_value(effect) != expected:
        raise ValueError("The optional top reveal has a rebound stack or mana value")
    return stack, mana_value


def cast_reveal_counter_program_dependencies(program):
    if not any(CAST_REVEAL_MANA_VALUE_COUNTER in effect for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("spell.cast", "battlefield") or program.target_schema is not None
            or program.cost_schema is not None or program.handlers or len(program.effects) != 1
            or FrozenMap(program.event_condition or {}) != FrozenMap(_OPPONENT_CAST)):
        raise ValueError("The reveal comparison requires its original untargeted opponent-cast trigger")
    validate_cast_reveal_counter(program.effects[0])
    return CAST_REVEAL_COUNTER_CAPABILITIES


def opponent_life_loss_condition():
    return {"field": "player", "op": "ne", "value": "$source.controller"}


def controller_life_loss_draw_condition():
    return {"field": "player", "op": "eq", "value": "$source.controller"}


def controller_life_loss_draw_effect():
    from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    return {
        "op": "draw", "player": "$controller", "private": True,
        "count": ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT).to_dict(),
    }


def controller_life_loss_draw_node_capabilities(*, effects, target_schema):
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY

    if (target_schema is not None or len(effects) != 1 or not isinstance(effects[0], Mapping)
            or effects[0].get("op") != "draw" or not isinstance(effects[0].get("count"), Mapping)
            or FrozenMap(effects[0]) != FrozenMap(controller_life_loss_draw_effect())
            or effects[0].get("private") is not True):
        return ()
    return (LIFE_LOSS_CAPABILITY, SCALAR_AMOUNT_CAPABILITY, "trigger.effect.fixed_event",
            "trigger.placement.apnap", "zone.draw.library_to_hand")


def _controller_life_loss_draw_program_dependencies(program, required):
    if (not required or (program.event, program.active_zone) != (LIFE_LOSS_EVENT, "battlefield")
            or program.cost_schema is not None or program.handlers
            or "one_or_more_event_batch" in program.coverage
            or program.event_condition != controller_life_loss_draw_condition()):
        raise ValueError("Controller life loss requires its exact distinct occurrence and private committed-amount draw")
    return required


def opponent_life_loss_effect(*, mill):
    from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    amount = ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT).to_dict()
    if mill:
        return {"op": "mill", "player": "$context.player", "count": amount}
    return {"op": "life", "player": "$controller", "delta": amount}


def opponent_life_loss_node_capabilities(*, effects, target_schema):
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY

    if target_schema is not None or len(effects) != 1 or not isinstance(effects[0], Mapping):
        return ()
    effect = FrozenMap(effects[0])
    if effect == FrozenMap(opponent_life_loss_effect(mill=False)):
        result = ("life.change.effect",)
    elif effect == FrozenMap(opponent_life_loss_effect(mill=True)):
        result = ("zone.mill.fixed", "zone.mill.event_recipient")
    else:
        return ()
    return (LIFE_LOSS_CAPABILITY, SCALAR_AMOUNT_CAPABILITY, "trigger.effect.fixed_event",
            "trigger.placement.apnap", *result)


def _opponent_life_loss_program_dependencies(program):
    required = opponent_life_loss_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if (not required or (program.event, program.active_zone) != (LIFE_LOSS_EVENT, "battlefield")
            or program.cost_schema is not None or program.handlers
            or "one_or_more_event_batch" in program.coverage
            or program.event_condition != opponent_life_loss_condition()):
        raise ValueError("Opponent loss requires its exact untargeted occurrence and one committed-amount result")
    return required


def noncreature_cast_condition():
    return {"not": {"field": "types", "op": "contains_any", "value": ["creature"]}}


def _cast_bird_amount():
    from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    return ScalarEffectAmountSpec(ScalarAmountOrigin.CAST_SPELL_MANA_VALUE,
                                  characteristic="mana_value", schema_version=8)


def _cast_bird_effect(amount):
    return {
        "op": "create_token", "controller": "$context.player", "quantity": amount, "name": "Bird Token",
        "characteristics": {
            "type_line": "Token Creature \u2014 Bird", "literal_token_name_version": 1,
            "colors": ["W", "U"], "power": "1", "toughness": "1", "keywords": ["Flying"],
        },
    }


def cast_event_counter_birds_effects():
    return (dict(_COUNTER), _cast_bird_effect(_cast_bird_amount().to_dict()))


def _cast_event_counter_birds_dependencies(effects, *, projected=False):
    if len(effects) != 2 or effects[0] != _COUNTER:
        return ()
    from ..replacement.immutable import FrozenMap
    from ..scalar_effect_amount_model import ScalarEffectAmountSpec, SCALAR_AMOUNT_CAPABILITY
    from ..cast_event_amounts import CAST_MANA_VALUE_CAPABILITY
    from .token_creation_capability_shapes import _fixed_token_effect_closure

    tokens = effects[1]
    if not isinstance(tokens, Mapping):
        return ()
    amount = tokens.get("quantity")
    expected_amount = 2 if projected else _cast_bird_amount().to_dict()
    if FrozenMap(tokens) != FrozenMap(_cast_bird_effect(expected_amount)):
        return ()
    if not projected:
        try:
            if ScalarEffectAmountSpec.from_dict(amount) != _cast_bird_amount():
                return ()
        except (TypeError, ValueError):
            return ()
    probe = {**_cast_bird_effect(2), "controller": "$controller"}
    token_owner = _fixed_token_effect_closure(probe)
    if token_owner is None:
        return ()
    return (*token_owner[0], CAST_EVENT_COUNTER_CAPABILITY, "stack.counter.effect",
            CAST_EVENT_COUNTER_BIRDS_CAPABILITY, CAST_MANA_VALUE_CAPABILITY, SCALAR_AMOUNT_CAPABILITY)


def _requires_cast_event_counter_birds(program):
    if CAST_EVENT_COUNTER_BIRDS_CAPABILITY in getattr(program, "capability_dependencies", ()):
        return True
    from ..scalar_effect_amount_model import ScalarAmountOrigin, scalar_amount_specs

    return (
        any(effect == _COUNTER for effect in program.effects)
        and any(isinstance(effect, Mapping) and effect.get("op") == "create_token"
                and effect.get("controller") == "$context.player"
                and any(spec.origin is ScalarAmountOrigin.CAST_SPELL_MANA_VALUE for spec in scalar_amount_specs(effect))
                for effect in program.effects)
    )


def _cast_event_counter_birds_program_dependencies(program):
    required = _cast_event_counter_birds_dependencies(program.effects)
    if (
        not required or (program.event, program.active_zone) != ("spell.cast", "battlefield")
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or thaw_value(program.event_condition) != noncreature_cast_condition()
        or "one_or_more_event_batch" in getattr(program, "coverage", ())
    ):
        raise ValueError("Cast counter and Bird tokens require the exact noncreature cast frame and original caster")
    return (*required, "trigger.event.normalized_spell_cast", "trigger.effect.fixed_event", "trigger.placement.apnap")


def cast_event_token_recipient_has_left_game(host, item, effect):
    if effect.get("op") != "create_token":
        return False
    semantic_key = getattr(item, "semantic_key", None)
    program = host.semantics.get(semantic_key) if semantic_key else None
    if program is None or CAST_EVENT_COUNTER_BIRDS_CAPABILITY not in program.capability_dependencies:
        return False
    from ..cast_event_amounts import committed_cast_mana_value_facts

    caster = committed_cast_mana_value_facts(item)["player"]
    if caster not in host.state.players:
        raise ValueError("Cast-event token creation has lost the original caster")
    return caster not in host.state.active_seats()


def zero_mana_spent_cast_condition():
    return {"field": _ZERO_MANA_FIELD, "op": "eq", "value": 0}


def uses_zero_mana_cast_condition(value):
    if isinstance(value, Mapping):
        return value.get("field") == _ZERO_MANA_FIELD or any(
            uses_zero_mana_cast_condition(value.get(key)) for key in ("all", "any", "not")
        )
    if isinstance(value, (list, tuple)):
        return any(uses_zero_mana_cast_condition(child) for child in value)
    return False


def _validate_zero_mana_cast_result(program):
    condition = thaw_value(program.event_condition)
    effects = tuple(program.effects)
    if effects == (_ZERO_MANA_DAMAGE,):
        if type(effects[0]["amount"]) is not int or condition != zero_mana_spent_cast_condition():
            raise ValueError("Zero-paid cast damage requires the exact any-player cast")
    elif effects != (_COUNTER,):
        raise ValueError("Zero mana spent requires its exact sealed cast result")
    if isinstance(condition, Mapping) and set(condition) == {"all"}:
        clauses = condition["all"]
        if not isinstance(clauses, list) or len(clauses) != 2 or clauses[0] != _OPPONENT_CAST:
            raise ValueError("Zero mana spent requires the exact opponent cast relation")
        condition = clauses[1]
    if (
        (program.event, program.active_zone) != ("spell.cast", "battlefield")
        or program.target_schema is not None or getattr(program, "cost_schema", None) is not None
        or getattr(program, "handlers", ()) or "intervening_condition" not in program.coverage
        or condition != zero_mana_spent_cast_condition() or type(condition["value"]) is not int
    ):
        raise ValueError("Zero mana spent requires the exact sealed cast trigger")


def zero_mana_cast_counter_recheck(program, context):
    if tuple(program.effects) != (_COUNTER,):
        raise ValueError("Zero mana spent counter requires the original spell counter result")
    return zero_mana_cast_result_recheck(program, context)


def zero_mana_cast_result_recheck(program, context):
    _validate_zero_mana_cast_result(program)
    spent = context.get(_ZERO_MANA_FIELD)
    stack = context.get("stack")
    return (
        context.get("event") == "spell.cast" and type(stack) is str and bool(stack)
        and type(spent) is int and spent == 0
    )


def _cast_trigger_sacrifice_counter_dependencies(effects):
    if len(effects) != 2:
        return ()
    sacrifice, counter = effects
    if sacrifice != fixed_source_sacrifice_effect() or counter != _COUNTER:
        return ()
    try:
        sacrifice_owners = fixed_source_sacrifice_dependencies(sacrifice)
    except (TypeError, ValueError):
        return ()
    return (*sacrifice_owners, CAST_EVENT_COUNTER_CAPABILITY,
            "stack.counter.effect", CAST_TRIGGER_SACRIFICE_COUNTER_CAPABILITY)


def _requires_cast_trigger_sacrifice_counter(program):
    if CAST_TRIGGER_SACRIFICE_COUNTER_CAPABILITY in getattr(program, "capability_dependencies", ()):
        return True
    return (
        any(isinstance(effect, Mapping) and SOURCE_SACRIFICE_FIELD in effect for effect in program.effects)
        and any(
            isinstance(effect, Mapping) and effect.get("op") == "counter_stack_target"
            and effect.get("stack") == "$context.stack" for effect in program.effects
        )
    )


def _cast_trigger_sacrifice_counter_program_dependencies(program):
    required = _cast_trigger_sacrifice_counter_dependencies(program.effects)
    if (
        not required or (program.event, program.active_zone) != ("spell.cast", "battlefield")
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or thaw_value(program.event_condition) not in (None, _OPPONENT_CAST)
        or "one_or_more_event_batch" in getattr(program, "coverage", ())
    ):
        raise ValueError("Source sacrifice and cast counter require their exact untargeted cast-trigger sequence")
    return (*required, "trigger.event.normalized_spell_cast", "trigger.effect.fixed_event", "trigger.placement.apnap")


def event_outcome_node_capabilities(*, effects, target_schema, mechanic_ids):
    from .received_damage_watch import watched_damage_node_capabilities

    if required := watched_damage_node_capabilities(effects, target_schema):
        return required
    del mechanic_ids

    if target_schema is not None: return ()
    from .source_subtype_progression import uses_ring_damage_outcome, ring_damage_outcome_dependencies
    if len(effects) == 1 and uses_ring_damage_outcome(effects[0]):
        try:
            return ring_damage_outcome_dependencies(effects[0])
        except ValueError:
            return ()
    from .event_counter_threshold import source_depletion_counter_node_capabilities
    from .combat_receiving_life import combat_receiving_life_node_capabilities

    from .opponent_draw_followup import distinct_draw_node_capabilities

    if required := distinct_draw_node_capabilities(effects, target_schema):
        return required
    if required := combat_receiving_life_node_capabilities(effects, target_schema):
        return required
    if required := source_depletion_counter_node_capabilities(effects, target_schema):
        return required
    if len(effects) == 1 and uses_cast_reveal_counter(effects[0]):
        try:
            validate_cast_reveal_counter(effects[0])
        except (TypeError, ValueError, KeyError):
            return ()
        return CAST_REVEAL_COUNTER_CAPABILITIES
    if required := _cast_event_counter_birds_dependencies(effects, projected=True):
        return required
    if required := _cast_trigger_sacrifice_counter_dependencies(effects):
        return required
    if tuple(effects) == (_COUNTER,): return (CAST_EVENT_COUNTER_CAPABILITY, 'stack.counter.effect')
    if tuple(effects) == (_LOSS,): return (PLAYER_LOSS_CAPABILITY,)
    return ()


def event_outcome_runtime_dependencies(program):
    from .controlled_creature_main_combat import controlled_main_combat_runtime_dependencies
    from .paid_attacking_extra_combat import paid_attacking_extra_combat_runtime_dependencies

    if dependencies := (controlled_main_combat_runtime_dependencies(program)
                        or paid_attacking_extra_combat_runtime_dependencies(program)):
        return dependencies
    from .apnap_library_reveals import library_reveal_round_program_dependencies
    if dependencies := library_reveal_round_program_dependencies(program):
        return dependencies
    from .airbend import airbend_creature_set_program_dependencies
    if dependencies := airbend_creature_set_program_dependencies(program):
        return dependencies
    from .attack_trigger_copy import program_dependencies as attack_copy_dependencies

    if dependencies := attack_copy_dependencies(program):
        return dependencies
    from .cast_or_attack_source_exile import cast_or_attack_source_exile_runtime_dependencies

    if dependencies := cast_or_attack_source_exile_runtime_dependencies(program):
        return dependencies
    from .blocked_damage_cast import program_dependencies as blocked_damage_dependencies

    if dependencies := blocked_damage_dependencies(program):
        return dependencies
    from .meld_pair_shapes import meld_pair_runtime_dependencies

    if dependencies := meld_pair_runtime_dependencies(program):
        return dependencies
    from .main_phase_combats import main_phase_combat_program_dependencies
    if dependencies := main_phase_combat_program_dependencies(program):
        return dependencies
    from .tap_attack_result import tap_attack_program_dependencies

    if owned := tap_attack_program_dependencies(program):
        return owned
    from .library_creature_arrival import arrival_program_dependencies

    if dependencies := arrival_program_dependencies(program):
        return dependencies
    from .received_damage_watch import watched_damage_runtime_dependencies

    if required := watched_damage_runtime_dependencies(program):
        return required
    from .combat_receiving_life import combat_receiving_life_runtime_dependencies

    if required := combat_receiving_life_runtime_dependencies(program):
        return required
    from .source_subtype_progression import ring_damage_outcome_program_dependencies

    if outcome := ring_damage_outcome_program_dependencies(program):
        return outcome
    from .faction_trigger_shapes import faction_trigger_runtime_dependencies
    from .paid_creature_library_search import paid_creature_search_program_dependencies
    if dependencies := paid_creature_search_program_dependencies(program):
        return dependencies
    from .paid_artifact_library_search import paid_artifact_search_runtime_dependencies

    if dependencies := paid_artifact_search_runtime_dependencies(program):
        return dependencies
    if dependencies := faction_trigger_runtime_dependencies(program):
        return dependencies
    from .opponent_draw_followup import distinct_draw_runtime_dependencies

    if dependencies := distinct_draw_runtime_dependencies(program):
        return dependencies
    from .controlled_subtype_damage_batch import controlled_subtype_damage_runtime_dependencies

    if (grouped := controlled_subtype_damage_runtime_dependencies(program)) is not None:
        return grouped
    from .fixed_opponent_land_search import opponent_land_search_program_dependencies

    if dependencies := opponent_land_search_program_dependencies(program):
        return dependencies
    from .created_token_fights import created_token_fight_program_dependencies

    if dependencies := created_token_fight_program_dependencies(program):
        return dependencies
    from .repeated_apnap_payment import payment_round_program_dependencies

    if dependencies := payment_round_program_dependencies(program):
        return dependencies
    from .private_search_play import private_search_play_program_dependencies

    if dependencies := private_search_play_program_dependencies(program):
        return dependencies
    from .bargain_search import bargain_search_program_dependencies

    if dependencies := bargain_search_program_dependencies(program):
        return dependencies
    from .extra_turn_shapes import (
        fixed_extra_turn_end_step_loss_runtime_dependencies, opponent_color_extra_turn_runtime_dependencies,
    )
    from .library_search_capability_shapes import (
        creature_subset_mana_value_search_runtime_dependencies,
        creature_stat_library_search_runtime_dependencies,
        literal_name_any_number_runtime_dependencies, recruiting_library_search_runtime_dependencies,
        target_name_library_search_runtime_dependencies, target_player_basic_land_search_runtime_dependencies,
    )

    from .exiled_target_search_shapes import exiled_target_search_runtime_dependencies

    if dependencies := target_player_basic_land_search_runtime_dependencies(program):
        return dependencies
    if dependencies := creature_subset_mana_value_search_runtime_dependencies(program):
        return dependencies
    if dependencies := creature_stat_library_search_runtime_dependencies(program):
        return dependencies
    if dependencies := exiled_target_search_runtime_dependencies(program):
        return dependencies
    if dependencies := recruiting_library_search_runtime_dependencies(program):
        return dependencies
    if dependencies := literal_name_any_number_runtime_dependencies(program):
        return dependencies
    if dependencies := target_name_library_search_runtime_dependencies(program):
        return dependencies
    from .dragon_graveyard_search_copy import dragon_graveyard_copy_search_runtime_dependencies

    if dependencies := dragon_graveyard_copy_search_runtime_dependencies(program):
        return dependencies
    if dependencies := opponent_color_extra_turn_runtime_dependencies(program):
        return dependencies
    if dependencies := fixed_extra_turn_end_step_loss_runtime_dependencies(program):
        return dependencies
    controller_draw = controller_life_loss_draw_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
    )
    if (CONTROLLER_LIFE_LOSS_DRAW_MECHANIC in program.coverage
            or (program.event == LIFE_LOSS_EVENT and controller_draw)):
        return _controller_life_loss_draw_program_dependencies(program, controller_draw)
    if (program.event == LIFE_LOSS_EVENT or LIFE_LOSS_CAPABILITY in program.capability_dependencies
            or OPPONENT_LIFE_LOSS_MECHANIC in program.coverage):
        return _opponent_life_loss_program_dependencies(program)
    if _requires_cast_trigger_sacrifice_counter(program):
        return _cast_trigger_sacrifice_counter_program_dependencies(program)
    if _requires_cast_event_counter_birds(program):
        return _cast_event_counter_birds_program_dependencies(program)
    if required := cast_reveal_counter_program_dependencies(program):
        return required
    from .shared_type_library import mandatory_shared_type_cast_program_dependencies

    if required := mandatory_shared_type_cast_program_dependencies(program):
        return required
    from .event_counter_threshold import source_depletion_counter_program_dependencies

    if required := source_depletion_counter_program_dependencies(program):
        return required
    from .paid_noncreature_counter_transform import paid_cast_transform_runtime_dependencies

    if required := paid_cast_transform_runtime_dependencies(program):
        return required
    if uses_zero_mana_cast_condition(program.event_condition):
        _validate_zero_mana_cast_result(program)
    required = set()
    for effect in program.effects:
        if not isinstance(effect, Mapping): continue
        if effect.get('op') == 'offer_optional_mana_payment' and effect.get('schema_version') == 6:
            from ..cast_source_sacrifice_payment import cast_source_sacrifice_runtime_dependencies
            required.update(cast_source_sacrifice_runtime_dependencies(program,effect))
        if effect.get('op') == 'counter_stack_target' and effect.get('stack') == '$context.stack':
            if (dict(effect) != _COUNTER or program.target_schema is not None or program.event != 'spell.cast'
                    or program.active_zone not in {'battlefield','command'}):
                raise ValueError('Cast-event counter requires the sealed cast spell and nontargeted trigger')
            required.update((CAST_EVENT_COUNTER_CAPABILITY, 'stack.counter.effect', 'trigger.event.normalized_spell_cast'))
        if effect.get('op') == 'lose_game':
            from .event_player_result_shapes import _event_player_reference_is_closed
            if (dict(effect) != _LOSS or program.target_schema is not None
                    or program.active_zone not in {'battlefield','command'}
                    or not _event_player_reference_is_closed(program, '$context.target')):
                raise ValueError('Player game loss requires its sealed damaged-player occurrence')
            required.add(PLAYER_LOSS_CAPABILITY)
    return tuple(sorted(required))


def event_outcome_program_is_closed(program):
    from ..meld_model import MELD_CAPABILITY
    from .combat_receiving_life import uses_combat_receiving_life
    from .faction_trigger_model import FACTION_DRAW_CAPABILITY
    from .opponent_draw_followup import DISTINCT_DRAW_CAPABILITY
    from .event_counter_threshold import SOURCE_DEPLETION_THRESHOLD_CAPABILITY
    from .paid_noncreature_counter_transform import CAPABILITY as PAID_CAST_TRANSFORM_CAPABILITY

    try: required = event_outcome_runtime_dependencies(program)
    except (TypeError,ValueError): return False
    return (
        bool(required)
        and (len(program.effects) == 1 or MELD_CAPABILITY in required or uses_combat_receiving_life(program.effects)
             or CAST_TRIGGER_SACRIFICE_COUNTER_CAPABILITY in required
             or CAST_EVENT_COUNTER_BIRDS_CAPABILITY in required
             or SOURCE_DEPLETION_THRESHOLD_CAPABILITY in required
             or PAID_CAST_TRANSFORM_CAPABILITY in required
             or FACTION_DRAW_CAPABILITY in required
             or DISTINCT_DRAW_CAPABILITY in required)
        and set(required).issubset(program.capability_dependencies)
    )
