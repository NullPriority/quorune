from __future__ import annotations

"""The closed choice to lend a permanent at the active opponent's upkeep."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..delayed_object_model import (
    DELAYED_SOURCE_CONVERT_DEPENDENCIES,
    DELAYED_SOURCE_CONVERT_VERSION,
    PERMANENT_CONVERT_CAPABILITY,
    RESOLVE_OBJECT_ACTION,
    SCHEDULE_OBJECT_ACTION,
    delayed_source_convert_effect,
)
from ..goad_instruction import GOAD_CAPABILITY
from ..replacement.immutable import FrozenMap
from ..spell_history_transform_model import SPELL_HISTORY_TRANSFORM_COVERAGE
from .effect_shape_walkers import nested_effects


SOURCE_UPKEEP_HANDOFF_FIELD = "source_upkeep_handoff"
SOURCE_UPKEEP_HANDOFF_CAPABILITY = "continuous.control.optional_upkeep_source_handoff"
SOURCE_UPKEEP_HANDOFF_MECHANIC = "fixed-optional-effect-choice"
SACRIFICE_PROHIBITION_CAPABILITY = "permanent.sacrifice.until_cleanup_prohibition"
SOURCE_UPKEEP_HANDOFF_DEPENDENCIES = (
    SOURCE_UPKEEP_HANDOFF_CAPABILITY,
    "effect.choice.optional_fixed",
    "continuous.control.fixed_resolution",
    "permanent.untap.effect",
    GOAD_CAPABILITY,
    SACRIFICE_PROHIBITION_CAPABILITY,
    PERMANENT_CONVERT_CAPABILITY,
    "trigger.effect.fixed_event",
    "trigger.placement.apnap",
)


def source_upkeep_handoff_condition() -> dict:
    return {"all": [
        {"field": "step", "op": "eq", "value": "upkeep"},
        {"field": "player", "op": "ne", "value": "$source.controller"},
    ]}


def source_upkeep_handoff_effect() -> dict:
    return {
        "op": "offer_optional_effect",
        "player": "$controller",
        "effects": [{
            "op": "gain_control",
            SOURCE_UPKEEP_HANDOFF_FIELD: 1,
            "card": "$source.zone_object",
            "recipient": "$context.player",
            "expected_transform_count": "$source.transform_count",
        }],
    }


def uses_source_upkeep_handoff(effect: Mapping) -> bool:
    if not isinstance(effect, Mapping) or effect.get("op") != "offer_optional_effect":
        return False
    children = effect.get("effects")
    return isinstance(children, (list, tuple)) and any(
        isinstance(child, Mapping) and SOURCE_UPKEEP_HANDOFF_FIELD in child for child in children
    )


def decode_source_upkeep_handoff(effect: Mapping, *, actor="$controller", resolved=False):
    if not uses_source_upkeep_handoff(effect) or len(effect["effects"]) != 1:
        raise ValueError("A source handoff requires its one closed optional choice")
    instruction = effect["effects"][0]
    if type(instruction[SOURCE_UPKEEP_HANDOFF_FIELD]) is not int:
        raise ValueError("A source handoff requires its exact instruction version")
    expected = source_upkeep_handoff_effect()
    if resolved:
        card = instruction.get("card")
        recipient = instruction.get("recipient")
        count = instruction.get("expected_transform_count")
        if (card is not None and (type(card) is not str or not card or card.startswith("$"))
                or type(recipient) is not str or not recipient or recipient.startswith("$") or recipient == actor
                or type(count) is not int or count < 0):
            raise ValueError("A source handoff needs its original opponent, source and conversion count")
        expected["player"] = actor
        expected["effects"][0].update(card=card, recipient=recipient, expected_transform_count=count)
    if FrozenMap(effect) != FrozenMap(expected):
        raise ValueError("A source handoff changed its printed instruction or bound controller")
    return instruction


def source_upkeep_handoff_node_capabilities(*, effects, target_schema, **_):
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        decode_source_upkeep_handoff(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    return SOURCE_UPKEEP_HANDOFF_DEPENDENCIES


def source_upkeep_handoff_runtime_dependencies(program):
    if not any(SOURCE_UPKEEP_HANDOFF_FIELD in child for child in nested_effects((program.effects, program.handlers))):
        return ()
    dependencies = source_upkeep_handoff_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if (not dependencies or program.handlers or program.cost_schema is not None or program.trigger_limit is not None
            or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(source_upkeep_handoff_condition())
            or not {CURRENT_ABILITY_FRAGMENT_COVERAGE, SPELL_HISTORY_TRANSFORM_COVERAGE} <= set(program.coverage)):
        raise ValueError("A source handoff requires its own opponent-upkeep trigger and retained conversion count")
    return dependencies


def _delayed_source_conversion_runtime_dependencies(program):
    effects = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                    if effect.get("op") in {SCHEDULE_OBJECT_ACTION, RESOLVE_OBJECT_ACTION}
                    and effect.get("schema_version") == DELAYED_SOURCE_CONVERT_VERSION)
    if not effects:
        return ()
    condition = FrozenMap({"all": [
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
        {"field": "source_logical_object_id", "op": "eq", "value": "$source.logical_object_id"},
    ]})
    if (tuple(FrozenMap(effect) for effect in effects) != (FrozenMap(delayed_source_convert_effect()),)
            or tuple(program.effects) != effects or program.handlers or program.target_schema is not None
            or program.cost_schema is not None or program.trigger_limit is not None
            or (program.event, program.active_zone) != ("damage.dealt.self", "battlefield")
            or FrozenMap(program.event_condition or {}) != condition
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage):
        raise ValueError("Delayed conversion requires its complete source combat damage to a player trigger")
    return DELAYED_SOURCE_CONVERT_DEPENDENCIES


def source_control_or_conversion_runtime_dependencies(program):
    return (*source_upkeep_handoff_runtime_dependencies(program),
            *_delayed_source_conversion_runtime_dependencies(program))
