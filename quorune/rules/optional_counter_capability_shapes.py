from __future__ import annotations

"""Capability shape for optional fixed counter event-trigger wrappers."""

from typing import Any, Iterable, Mapping, Sequence

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..replacement.immutable import FrozenMap, thaw_value
from .node_capability_shapes import (
    fixed_counter_placement_batch_node_capabilities,
    fixed_counter_placement_node_capabilities,
    fixed_counter_placement_set_node_capabilities,
    fixed_counter_placement_target_set_node_capabilities,
    fixed_player_counter_placement_node_capabilities,
)


OWNED_GRAVEYARD_QUEST_COUNTER_CAPABILITY = "counter.producer.optional_owned_graveyard_source_quest"
CONTROLLED_COMBAT_QUEST_COUNTER_CAPABILITY = "counter.producer.optional_controlled_combat_source_quest"
_OWNED_GRAVEYARD_CARD = {"all": [
    {"field": "token", "op": "eq", "value": False},
    {"field": "owner", "op": "eq", "value": "$source.controller"},
]}
_CONTROLLED_CREATURE_COMBAT_DAMAGE = {"all": [
    {"all": [
        {"field": "source_controller", "op": "eq", "value": "$source.controller"},
        {"field": "source_types", "op": "contains_any", "value": ["creature"]},
    ]},
    {"field": "target_kind", "op": "eq", "value": "permanent"},
    {"field": "target_types", "op": "contains_any", "value": ["creature"]},
    {"field": "combat", "op": "truthy", "value": True},
]}
_SOURCE_QUEST_RESULT = {
    "op": "place_counters", "card": "$source.zone_object", "counter": "quest",
    "amount": 1, "source": "$source",
}


_OPPONENT_DAMAGE_COUNTER_TEMPLATE = "fixed-counter-opponent-damage-source-counters-optional-v1"
RECIPIENT_DAMAGE_COUNTER_VARIANT = "recipient_damage_optional_source_counters"
RECIPIENT_DAMAGE_COUNTER_BINDING_TEMPLATE = "fixed-counter-recipient-damage-source-counters-v1"
_RECIPIENT_DAMAGE_COUNTER_TEMPLATE = "fixed-counter-recipient-damage-source-counters-optional-v1"


def damage_to_controller_condition():
    return {"all": [
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "target", "op": "eq", "value": "$source.controller"},
    ]}


def opponent_damage_to_controller_condition():
    controlled_zone = {"field": "source_zone", "op": "in", "value": ["battlefield", "stack"]}

    def opposing_seat(field):
        return [
            {"field": field, "op": "truthy", "value": True},
            {"field": field, "op": "ne", "value": "$source.controller"},
        ]

    return {"all": [
        *damage_to_controller_condition()["all"],
        {"any": [
            {"all": [controlled_zone, *opposing_seat("source_controller")]},
            {"all": [{"field": "source_zone", "op": "in", "value": ["graveyard", "exile", "command", "hand", "library"]}, *opposing_seat("source_owner")]},
        ]},
    ]}


def opponent_damage_source_counter_effect():
    from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    return {
        "op": "place_counters", "card": "$source.zone_object", "counter": "+1/+1",
        "amount": ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT).to_dict(), "source": "$source",
    }


def _opponent_damage_source_counter_matches(effects, target_schema):
    from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    if target_schema is not None or len(effects) != 1:
        return False
    child = effects[0]
    if FrozenMap(child) != FrozenMap(opponent_damage_source_counter_effect()):
        return False
    try:
        return ScalarEffectAmountSpec.from_dict(child["amount"]) == ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT)
    except (TypeError, ValueError):
        return False


def _damage_source_counter_is_closed(event, condition, effects, target_schema, *, expected):
    return (
        event == "damage.dealt" and isinstance(condition, Mapping)
        and FrozenMap(condition) == FrozenMap(expected)
        and _opponent_damage_source_counter_matches(effects, target_schema)
    )


def opponent_damage_source_counter_is_closed(event, condition, effects, target_schema):
    return _damage_source_counter_is_closed(event, condition, effects, target_schema,
        expected=opponent_damage_to_controller_condition())


def recipient_damage_source_counter_is_closed(event, condition, effects, target_schema):
    return _damage_source_counter_is_closed(event, condition, effects, target_schema,
        expected=damage_to_controller_condition())


def _opponent_damage_source_counter_dependencies():
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY

    return (
        SCALAR_AMOUNT_CAPABILITY, "counter.producer.fixed_effect", "counter.producer.fixed_public_source_event",
        "counter.producer.optional_fixed_event_trigger", "trigger.event.normalized_damage", "trigger.placement.apnap",
    )


def _damage_source_counter_wrapper(program):
    wrapper = program.effects[0] if len(program.effects) == 1 else {}
    child = wrapper.get("effect")
    return wrapper, (child,) if isinstance(child, Mapping) else ()


def _require_damage_source_counter_frame(program, wrapper, *, closed):
    if (
        not closed or program.active_zone != "battlefield" or program.cost_schema is not None or program.handlers
        or "one_or_more_event_batch" in program.coverage
        or set(wrapper) != {"op", "player", "effect"}
        or wrapper.get("op") != "offer_optional_counter_placement" or wrapper.get("player") != "$controller"
    ):
        raise ValueError("Optional damage counters require the original recipient, source scope and committed amount")
    return _opponent_damage_source_counter_dependencies()


def opponent_damage_source_counter_runtime_dependencies(program):
    wrapper, nested = _damage_source_counter_wrapper(program)
    marked = _OPPONENT_DAMAGE_COUNTER_TEMPLATE in str(program.provenance.get("template_id") or "").split("+")
    if not marked and not (
        wrapper.get("op") == "offer_optional_counter_placement"
        and _opponent_damage_source_counter_matches(nested, program.target_schema)
    ):
        return ()
    return _require_damage_source_counter_frame(program, wrapper, closed=opponent_damage_source_counter_is_closed(
        program.event, program.event_condition, nested, program.target_schema))


def recipient_damage_source_counter_runtime_dependencies(program):
    templates = str(program.provenance.get("template_id") or "").split("+")
    if _RECIPIENT_DAMAGE_COUNTER_TEMPLATE not in templates:
        return ()
    wrapper, nested = _damage_source_counter_wrapper(program)
    closed = (_OPPONENT_DAMAGE_COUNTER_TEMPLATE not in templates
              and CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage
              and recipient_damage_source_counter_is_closed(
                  program.event, program.event_condition, nested, program.target_schema))
    return _require_damage_source_counter_frame(program, wrapper, closed=closed)


def _source_quest_counter_is_closed(effects, target_schema):
    return (
        target_schema is None and len(effects) == 1 and effects[0] == _SOURCE_QUEST_RESULT
        and type(effects[0]["amount"]) is int
    )


def owned_graveyard_quest_counter_is_closed(event, condition, effects, target_schema):
    if (
        event != "card.enter_graveyard"
        or not isinstance(condition, Mapping)
        or FrozenMap(condition) != FrozenMap(_OWNED_GRAVEYARD_CARD)
        or not _source_quest_counter_is_closed(effects, target_schema)
    ):
        return False
    return thaw_value(FrozenMap(condition))["all"][0]["value"] is False


def controlled_combat_quest_counter_is_closed(event, condition, effects, target_schema):
    if (
        event != "damage.dealt"
        or not isinstance(condition, Mapping)
        or FrozenMap(condition) != FrozenMap(_CONTROLLED_CREATURE_COMBAT_DAMAGE)
        or not _source_quest_counter_is_closed(effects, target_schema)
    ):
        return False
    return thaw_value(FrozenMap(condition))["all"][-1]["value"] is True


def _source_quest_counter_runtime_dependencies(program, validator, capability, event_owner, error):
    wrapper = program.effects[0] if len(program.effects) == 1 else {}
    nested = (wrapper["effect"],) if isinstance(wrapper.get("effect"), Mapping) else ()
    closed = validator(program.event, program.event_condition, nested, program.target_schema)
    if not closed and capability not in program.capability_dependencies:
        return ()
    if (
        not closed or program.active_zone != "battlefield" or program.cost_schema is not None or program.handlers
        or "one_or_more_event_batch" in program.coverage
        or set(wrapper) != {"op", "player", "effect"}
        or wrapper.get("op") != "offer_optional_counter_placement"
        or wrapper.get("player") != "$controller"
    ):
        raise ValueError(error)
    return (
        capability, "counter.producer.optional_fixed_event_trigger",
        "counter.producer.fixed_public_source_event", "counter.producer.fixed_effect",
        event_owner, "trigger.placement.apnap",
    )


def owned_graveyard_quest_counter_runtime_dependencies(program):
    return _source_quest_counter_runtime_dependencies(
        program, owned_graveyard_quest_counter_is_closed, OWNED_GRAVEYARD_QUEST_COUNTER_CAPABILITY,
        "trigger.event.normalized_anywhere_graveyard",
        "The optional quest result requires one owned graveyard card occurrence and the original source",
    )


def controlled_combat_quest_counter_runtime_dependencies(program):
    return _source_quest_counter_runtime_dependencies(
        program, controlled_combat_quest_counter_is_closed, CONTROLLED_COMBAT_QUEST_COUNTER_CAPABILITY,
        "trigger.event.normalized_damage",
        "The optional quest result requires one controlled creature combat damage occurrence to a creature",
    )


def optional_public_counter_event_dependencies(event, condition, mechanics, effects, target_schema):
    if (not {"fixed-public-event-source-counter", "fixed-public-event-target-counter"}.intersection(mechanics)
            or event in {"permanent.sacrificed", "card.discarded"}):
        return ()
    if (opponent_damage_source_counter_is_closed(event, condition, effects, target_schema)
            or recipient_damage_source_counter_is_closed(event, condition, effects, target_schema)):
        return _opponent_damage_source_counter_dependencies()
    if owned_graveyard_quest_counter_is_closed(event, condition, effects, target_schema):
        return (OWNED_GRAVEYARD_QUEST_COUNTER_CAPABILITY,)
    if controlled_combat_quest_counter_is_closed(event, condition, effects, target_schema):
        return (CONTROLLED_COMBAT_QUEST_COUNTER_CAPABILITY,)
    return None


def optional_fixed_counter_event_trigger_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one optional wrapper around one closed counter effect."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        "optional-fixed-counter-event-trigger" not in mechanics
        or len(effects) != 1
    ):
        return ()
    wrapper = effects[0]
    if (
        set(wrapper) != {"op", "player", "effect"}
        or wrapper.get("op") != "offer_optional_counter_placement"
        or wrapper.get("player") != "$controller"
        or not isinstance(wrapper.get("effect"), Mapping)
    ):
        return ()
    nested_effects = (wrapper["effect"],)
    nested_dependencies: set[str] = set()
    for resolver in (
        fixed_counter_placement_batch_node_capabilities,
        fixed_counter_placement_node_capabilities,
        fixed_counter_placement_set_node_capabilities,
        fixed_counter_placement_target_set_node_capabilities,
        fixed_player_counter_placement_node_capabilities,
    ):
        nested_dependencies.update(
            resolver(
                effects=nested_effects,
                target_schema=target_schema,
                mechanic_ids=mechanics,
            )
        )
    if not any(
        dependency.startswith("counter.producer.")
        for dependency in nested_dependencies
    ):
        return ()
    return (
        "counter.producer.optional_fixed_event_trigger",
        *sorted(nested_dependencies),
    )


__all__ = ["optional_fixed_counter_event_trigger_node_capabilities"]
