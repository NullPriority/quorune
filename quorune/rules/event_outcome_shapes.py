"""Payload-derived requirements for exact cast and damage occurrence results."""
from collections.abc import Mapping

from ..life_change import LIFE_LOSS_CAPABILITY, LIFE_LOSS_EVENT
from ..replacement.immutable import FrozenMap, thaw_value
from ..source_sacrifice_model import (
    SOURCE_SACRIFICE_FIELD, fixed_source_sacrifice_dependencies, fixed_source_sacrifice_effect,
)

CAST_EVENT_COUNTER_CAPABILITY = 'stack.counter.cast_event_spell'
PLAYER_LOSS_CAPABILITY = 'game.loss.effect'
CAST_TRIGGER_SACRIFICE_COUNTER_CAPABILITY = "resolution.effect.cast_trigger_source_sacrifice_counter"
CAST_EVENT_COUNTER_BIRDS_CAPABILITY = "resolution.effect.cast_event_counter_mana_value_birds"
OPPONENT_LIFE_LOSS_MECHANIC = "opponent-life-loss-result"
_COUNTER = {'op':'counter_stack_target','stack':'$context.stack'}
_LOSS = {'op':'lose_game','player':'$context.target'}

_ZERO_MANA_FIELD = "mana_spent_total"
_ZERO_MANA_DAMAGE = {"op": "damage", "source": "$source", "target": "$context.player", "amount": 5}
_OPPONENT_CAST = {"field": "controller", "op": "ne", "value": "$source.controller"}


def opponent_life_loss_condition():
    return {"field": "player", "op": "ne", "value": "$source.controller"}


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
    del mechanic_ids
    if target_schema is not None: return ()
    from .event_counter_threshold import source_depletion_counter_node_capabilities

    if required := source_depletion_counter_node_capabilities(effects, target_schema):
        return required
    if required := _cast_event_counter_birds_dependencies(effects, projected=True):
        return required
    if required := _cast_trigger_sacrifice_counter_dependencies(effects):
        return required
    if tuple(effects) == (_COUNTER,): return (CAST_EVENT_COUNTER_CAPABILITY, 'stack.counter.effect')
    if tuple(effects) == (_LOSS,): return (PLAYER_LOSS_CAPABILITY,)
    return ()


def event_outcome_runtime_dependencies(program):
    from .extra_turn_shapes import (
        fixed_extra_turn_end_step_loss_runtime_dependencies, opponent_color_extra_turn_runtime_dependencies,
    )
    from .library_search_capability_shapes import (
        literal_name_any_number_runtime_dependencies, recruiting_library_search_runtime_dependencies,
        target_name_library_search_runtime_dependencies,
    )

    if dependencies := recruiting_library_search_runtime_dependencies(program):
        return dependencies
    if dependencies := literal_name_any_number_runtime_dependencies(program):
        return dependencies
    if dependencies := target_name_library_search_runtime_dependencies(program):
        return dependencies
    if dependencies := opponent_color_extra_turn_runtime_dependencies(program):
        return dependencies
    if dependencies := fixed_extra_turn_end_step_loss_runtime_dependencies(program):
        return dependencies
    if (program.event == LIFE_LOSS_EVENT or LIFE_LOSS_CAPABILITY in program.capability_dependencies
            or OPPONENT_LIFE_LOSS_MECHANIC in program.coverage):
        return _opponent_life_loss_program_dependencies(program)
    if _requires_cast_trigger_sacrifice_counter(program):
        return _cast_trigger_sacrifice_counter_program_dependencies(program)
    if _requires_cast_event_counter_birds(program):
        return _cast_event_counter_birds_program_dependencies(program)
    from .event_counter_threshold import source_depletion_counter_program_dependencies

    if required := source_depletion_counter_program_dependencies(program):
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
    from .event_counter_threshold import SOURCE_DEPLETION_THRESHOLD_CAPABILITY

    try: required = event_outcome_runtime_dependencies(program)
    except (TypeError,ValueError): return False
    return (
        bool(required)
        and (len(program.effects) == 1
             or CAST_TRIGGER_SACRIFICE_COUNTER_CAPABILITY in required
             or CAST_EVENT_COUNTER_BIRDS_CAPABILITY in required
             or SOURCE_DEPLETION_THRESHOLD_CAPABILITY in required)
        and set(required).issubset(program.capability_dependencies)
    )
