from __future__ import annotations

"""Closed extra-turn effect shapes over the canonical turn queue."""

from collections.abc import Mapping

from ..compiler.extra_turn_templates import (
    EXTRA_TURN_END_STEP_LOSS_FIELD, FIXED_EXTRA_TURN_CAPABILITY, FIXED_EXTRA_TURN_MECHANIC,
    OPPONENT_CAST_EXTRA_TURN_COLOR_FIELD, fixed_extra_turn_end_step_loss_effect, opponent_color_extra_turn_effect,
)
from ..public_history_query_model import SPELL_COLOR_HISTORY_FIELD

SCHEDULED_EXTRA_TURN_END_STEP_LOSS = "scheduled_extra_turn_end_step_loss"
_EXTRA_TURN_LOSS_DEPENDENCIES = (FIXED_EXTRA_TURN_CAPABILITY, "game.loss.effect", "trigger.placement.apnap")
_LOCKED_GRANT_DEPENDENCIES = ("continuous.resolution.fixed_keyword_zone_object", "permanent.indestructible.ordinary")


def _locked_grant_instruction(effect):
    return (effect.get("op") == "grant_zone_object_keyword"
            and ("scope" in effect or effect.get("schema_version") == 2))


def _extra_turn_end_step_loss_dependencies(effects, target_schema):
    from ..zone_object_keyword_model import validate_controlled_creature_indestructible

    if target_schema is not None or len(effects) not in (1, 2):
        raise ValueError("Extra-turn loss requires its exact untargeted results")
    validate_extra_turn_end_step_loss_effect(effects[-1])
    if len(effects) == 2:
        validate_controlled_creature_indestructible(effects[0])
        return (*_EXTRA_TURN_LOSS_DEPENDENCIES, *_LOCKED_GRANT_DEPENDENCIES)
    return _EXTRA_TURN_LOSS_DEPENDENCIES


def validate_extra_turn_end_step_loss_effect(effect, *, materialized=False):
    expected = fixed_extra_turn_end_step_loss_effect()
    values = dict(effect)
    source = None
    if materialized:
        from ..semantic_runtime.context import SemanticSourceContext

        frame = values.pop("_runtime_source", None)
        if not isinstance(frame, Mapping) or set(frame) != {"stack_ref", "object_id", "logical_object_id", "card_ref"}:
            raise ValueError("Extra turn loss requires the sealed resolving source")
        source = SemanticSourceContext(**frame)
        for key in ("player", "source"):
            value = values.get(key)
            if type(value) is not str or not value or value != value.strip() or value.startswith("$"):
                raise ValueError("Extra turn loss requires a materialized controller and source")
            expected[key] = value
        if values["source"] != (source.card_ref or source.stack_ref):
            raise ValueError("Extra turn loss requires the original spell source")
    if values != expected or type(values[EXTRA_TURN_END_STEP_LOSS_FIELD]) is not int:
        raise ValueError("Extra turn loss requires its exact versioned controller instruction")
    return source


def extra_turn_end_step_loss_condition(turn_id):
    if (type(turn_id) is not str or not turn_id.startswith("X")
            or not turn_id[1:].isascii() or not turn_id[1:].isdecimal()):
        raise ValueError("Extra turn loss requires one scheduled extra turn ID")
    return {
        "phase": "ending", "step": "end_step",
        SCHEDULED_EXTRA_TURN_END_STEP_LOSS: {"schema_version": 1, "turn_id": turn_id},
    }


def extra_turn_end_step_loss_template(controller):
    return {"context": {"dynamic_effects": [{"op": "lose_game", "player": controller}]}}


def extra_turn_end_step_loss_trigger_turn(trigger):
    marked = trigger.condition.get(SCHEDULED_EXTRA_TURN_END_STEP_LOSS)
    if (not isinstance(marked, Mapping) or type(marked.get("schema_version")) is not int
            or trigger.event_kind != "step.begin" or trigger.once is not True
            or trigger.condition != extra_turn_end_step_loss_condition(marked.get("turn_id"))
            or trigger.stack_template != extra_turn_end_step_loss_template(trigger.controller)):
        raise ValueError("The extra-turn loss trigger changed its exact boundary or result")
    return marked["turn_id"]


def uses_opponent_color_extra_turn(effect):
    return effect.get("op") == "extra_turn" and (
        OPPONENT_CAST_EXTRA_TURN_COLOR_FIELD in effect or SPELL_COLOR_HISTORY_FIELD in effect
    )


def validate_opponent_color_extra_turn(effect, *, actor=None):
    color = effect.get(OPPONENT_CAST_EXTRA_TURN_COLOR_FIELD)
    if type(color) is not str or color not in {"W", "U", "B", "R", "G"}:
        raise ValueError("Conditional extra turns require one canonical opposing spell color")
    expected = opponent_color_extra_turn_effect(color)
    values = dict(effect)
    if actor is not None:
        from ..semantic_runtime.context import SemanticSourceContext

        raw = values.pop("_runtime_source", None)
        if not isinstance(raw, Mapping) or set(raw) != {"stack_ref", "object_id", "logical_object_id", "card_ref"}:
            raise ValueError("Conditional extra turns require the sealed resolution source")
        source = SemanticSourceContext(**raw)
        expected.update(player=actor, source=source.card_ref or source.stack_ref)
    if values != expected or type(values[SPELL_COLOR_HISTORY_FIELD]) is not int:
        raise ValueError("Conditional extra turns require the exact controller and sealed color predicate")
    return color


def opponent_color_extra_turn_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(uses_opponent_color_extra_turn(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack") or program.handlers
            or program.target_schema is not None or program.cost_schema is not None
            or program.event_condition is not None or len(program.effects) != 1):
        raise ValueError("Conditional extra turns require one untargeted resolving spell")
    validate_opponent_color_extra_turn(program.effects[0])
    return (FIXED_EXTRA_TURN_CAPABILITY,)


def fixed_extra_turn_end_step_loss_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(EXTRA_TURN_END_STEP_LOSS_FIELD in effect or _locked_grant_instruction(effect)
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack")
            or program.cost_schema is not None or program.handlers):
        raise ValueError("Extra-turn loss requires its untargeted spell resolution")
    return _extra_turn_end_step_loss_dependencies(program.effects, program.target_schema)


def fixed_extra_turn_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (FIXED_EXTRA_TURN_MECHANIC not in mechanic_ids or not isinstance(effects, (list, tuple))
            or not 1 <= len(effects) <= 10):
        return ()
    if len(effects) == 1 and isinstance(effects[0], Mapping) and uses_opponent_color_extra_turn(effects[0]):
        if target_schema is not None:
            return ()
        try:
            validate_opponent_color_extra_turn(effects[0])
        except (TypeError, ValueError):
            return ()
        return (FIXED_EXTRA_TURN_CAPABILITY,)
    if all(isinstance(effect, Mapping) for effect in effects) and any(
        EXTRA_TURN_END_STEP_LOSS_FIELD in effect or _locked_grant_instruction(effect) for effect in effects
    ):
        try:
            return _extra_turn_end_step_loss_dependencies(effects, target_schema)
        except (TypeError, ValueError):
            return ()
    players = []
    for effect in effects:
        if (not isinstance(effect, Mapping) or set(effect) != {"op", "player", "source"}
                or effect.get("op") != "extra_turn" or effect.get("source") != "$source"
                or type(effect.get("player")) is not str
                or effect["player"] not in ("$controller", "$target.0")):
            return ()
        players.append(effect["player"])
    if len(set(players)) != 1:
        return ()
    targeted = players[0] == "$target.0"
    if not targeted:
        return (FIXED_EXTRA_TURN_CAPABILITY,) if target_schema is None else ()
    if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or target_schema not in (
        {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1},
        {"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1},
    )):
        return ()
    return (FIXED_EXTRA_TURN_CAPABILITY, "target.revalidate_resolution")


def is_closed_fixed_extra_turn_program(program):
    required = fixed_extra_turn_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)
