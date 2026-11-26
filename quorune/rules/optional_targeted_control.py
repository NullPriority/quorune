"""A resolving choice for one already revalidated permanent and its ordered control result."""

from __future__ import annotations

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects


OPTIONAL_TARGETED_CONTROL_VERSION = 6
OPTIONAL_TARGETED_CONTROL_CAPABILITY = "continuous.control.optional_targeted_temporary"
_CONTROL_MECHANICS = (
    "fixed-resolution-control", "cr-115-targets", "cr-611-continuous-effects", "tap-and-untap", "haste",
)
OPTIONAL_TARGETED_CONTROL_MECHANICS = ("fixed-optional-effect-choice", *_CONTROL_MECHANICS)
_COMPONENT_MECHANICS = ("closed-effect-program", "bound-effect-program", *_CONTROL_MECHANICS)


def optional_targeted_control_schema():
    return {"zones": ["battlefield"], "categories": ["permanent"], "count": 1}


def optional_targeted_control_condition_matches(value):
    return thaw_value(value) == {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "upkeep"},
    ]}


def optional_targeted_control_effect(*, actor="$controller", target="$target.0", source="$source"):
    return {
        "op": "offer_optional_effect", "player": actor, "schema_version": OPTIONAL_TARGETED_CONTROL_VERSION,
        "effects": [
            {"op": "gain_control", "card": target, "controller": actor,
             "duration": "until_end_of_turn", "source": source},
            {"op": "untap", "card": target},
            {"op": "grant_keyword_until_end_of_turn", "card": target, "keyword": "Haste"},
        ],
    }


def is_optional_targeted_control(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "offer_optional_effect"
            and effect.get("schema_version") == OPTIONAL_TARGETED_CONTROL_VERSION)


def validate_optional_targeted_control(effect, *, actor="$controller", resolved=False):
    if not is_optional_targeted_control(effect) or type(effect["schema_version"]) is not int:
        raise ValueError("Optional targeted control requires its exact descriptor version")
    value = thaw_value(FrozenMap(effect))
    if not resolved:
        if value != optional_targeted_control_effect():
            raise ValueError("Optional targeted control requires its fixed same-target result")
        return "$target.0"
    nested = value.get("effects")
    if not isinstance(nested, list) or len(nested) != 3 or not all(isinstance(child, dict) for child in nested):
        raise ValueError("Optional targeted control requires all three ordered results")
    target, source = nested[0].get("card"), nested[0].get("source")
    if (any(type(ref) is not str or not ref or ref.startswith("$") for ref in (target, source))
            or value != optional_targeted_control_effect(actor=actor, target=target, source=source)):
        raise ValueError("Optional targeted control changed its original target, actor or result")
    return target


def optional_targeted_control_node_capabilities(*, effects, target_schema):
    if (len(effects) != 1 or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or thaw_value(target_schema) != optional_targeted_control_schema()):
        return ()
    try:
        validate_optional_targeted_control(effects[0])
    except (TypeError, ValueError):
        return ()
    from .bound_effect_program_shapes import bound_effect_program_node_capabilities

    owned = bound_effect_program_node_capabilities(
        effects=effects[0]["effects"], target_schema=target_schema,
        mechanic_ids=_COMPONENT_MECHANICS,
    )
    return tuple(sorted({OPTIONAL_TARGETED_CONTROL_CAPABILITY, "effect.choice.optional_fixed", *owned})) if owned else ()


def optional_targeted_control_runtime_dependencies(program):
    if not any(is_optional_targeted_control(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = optional_targeted_control_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if (not required or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or not optional_targeted_control_condition_matches(program.event_condition)
            or program.cost_schema is not None or program.handlers or program.trigger_limit is not None):
        raise ValueError("Optional targeted control requires its complete own-upkeep trigger")
    return required
