from __future__ import annotations

"""One end-step token count over sealed controlled-creature counter placements."""

from ..replacement.immutable import FrozenMap
from ..turn_history_model import CONTROLLED_CREATURE_COUNTER_HISTORY_EVENT


CAPABILITY = "token.creation.controlled_creature_counter_history"
COUNT_FIELD = "controlled_creature_counter_token_version"
END_STEP_CONDITION = FrozenMap({"all": [
    {"field": "player", "op": "eq", "value": "$source.controller"},
    {"field": "step", "op": "eq", "value": "end_step"},
]})
MECHANIC = "cr-111-tokens"
_TEXT = (
    "create a 1/1 green insect creature token for each +1/+1 counter you've put "
    "on creatures under your control this turn."
)


def _insect_token(player, count):
    return {"op": "create_token", "controller": player, "name": "Insect Token", "quantity": count,
            "characteristics": {"type_line": "Token Creature — Insect", "literal_token_name_version": 1,
                                "colors": ["G"], "power": "1", "toughness": "1"}}


def controlled_creature_counter_token_effect():
    return {**_insect_token("$controller", 1), COUNT_FIELD: 1}


def controlled_creature_counter_token_result(binding, body):
    if ((binding.event.value, binding.active_zone) != ("step.begin", "battlefield")
            or FrozenMap(binding.event_condition or {}) != END_STEP_CONDITION or body != binding.body
            or " ".join(body.replace("’", "'").casefold().split()) != _TEXT):
        return None
    return ("controlled-creature-counter-history-insect-tokens-v1",
            (controlled_creature_counter_token_effect(),), None, (MECHANIC,))


def controlled_creature_counter_token_capabilities(effect, target_schema, mechanics):
    if (target_schema is not None or MECHANIC not in mechanics
            or FrozenMap(effect) != FrozenMap(controlled_creature_counter_token_effect())
            or type(effect.get(COUNT_FIELD)) is not int or type(effect.get("quantity")) is not int):
        return ()
    return CAPABILITY, "token.creation.fixed_definition"


def controlled_creature_counter_token_runtime_dependencies(program):
    from .effect_shape_walkers import contains_nested_key

    marked = CAPABILITY in program.capability_dependencies or contains_nested_key(
        (program.effects, program.target_schema, program.handlers), COUNT_FIELD,
    )
    if not marked:
        return ()
    required = controlled_creature_counter_token_capabilities(
        program.effects[0], program.target_schema, program.coverage,
    ) if len(program.effects) == 1 else ()
    if (not required or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or program.cost_schema is not None or program.handlers
            or FrozenMap(program.event_condition or {}) != END_STEP_CONDITION):
        raise ValueError("Controlled-creature counter tokens require the original controller end-step trigger")
    return (*required, "trigger.effect.fixed_event", "trigger.placement.apnap")


def bind_controlled_creature_counter_tokens(host, item, effect):
    history = host.state.turn_history
    if (not controlled_creature_counter_token_capabilities(effect, None, (MECHANIC,))
            or item.kind != "triggered_ability" or item.context.get("currently_resolving") is not True
            or item.context.get("event") != "step.begin" or item.context.get("step") != "end_step"
            or item.context.get("player") != item.controller):
        raise ValueError("Controlled-creature counter tokens require the actual resolving controller end step")
    if (history is None or history.turn_sequence != host.state.turn_sequence
            or history.controlled_creature_counter_history_version != 1):
        raise ValueError("Controlled-creature counter tokens require complete current placement history")
    count = sum(row.amount for row in history.events
                if row.kind == CONTROLLED_CREATURE_COUNTER_HISTORY_EVENT and row.actor == item.controller)
    return _insect_token(item.controller, count) if count else None
