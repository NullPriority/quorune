"""Payload-derived requirements for exact cast and damage occurrence results."""
from collections.abc import Mapping

from ..replacement.immutable import thaw_value

CAST_EVENT_COUNTER_CAPABILITY = 'stack.counter.cast_event_spell'
PLAYER_LOSS_CAPABILITY = 'game.loss.effect'
_COUNTER = {'op':'counter_stack_target','stack':'$context.stack'}
_LOSS = {'op':'lose_game','player':'$context.target'}

_ZERO_MANA_FIELD = "mana_spent_total"
_ZERO_MANA_DAMAGE = {"op": "damage", "source": "$source", "target": "$context.player", "amount": 5}
_OPPONENT_CAST = {"field": "controller", "op": "ne", "value": "$source.controller"}


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


def event_outcome_node_capabilities(*, effects, target_schema, mechanic_ids):
    del mechanic_ids
    if target_schema is not None: return ()
    if tuple(effects) == (_COUNTER,): return (CAST_EVENT_COUNTER_CAPABILITY, 'stack.counter.effect')
    if tuple(effects) == (_LOSS,): return (PLAYER_LOSS_CAPABILITY,)
    return ()


def event_outcome_runtime_dependencies(program):
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
    try: required = event_outcome_runtime_dependencies(program)
    except (TypeError,ValueError): return False
    return bool(required) and len(program.effects) == 1 and set(required).issubset(program.capability_dependencies)
