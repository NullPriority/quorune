from __future__ import annotations

"""Exact capability shape for the paired linked exile/return instruction."""

from typing import Any, Iterable, Mapping, Sequence

from ..linked_ability_exile_model import LINKED_ABILITY_EXILE_CAPABILITY
from ..linked_exile_return_model import (
    CONDITIONAL_EXILE_CAPABILITY, LINKED_ABILITY_BINDING_PREFIX, LINKED_ABILITY_TIMING, LINKED_EXILE_RETURN_CAPABILITY,
    LINKED_EXILE_RETURN_MECHANIC, LINKED_EXILE_RETURN_OPERATION, LinkedExileReturnSpec, validate_linked_instruction,
)
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..targets import TargetGroup


def linked_ability_exile_node_capabilities(*, effects, target_schema, mechanic_ids):
    if LINKED_EXILE_RETURN_MECHANIC not in mechanic_ids or len(effects) != 1:
        return ()
    effect = effects[0]
    if not isinstance(effect, Mapping) or effect.get("op") != LINKED_EXILE_RETURN_OPERATION:
        return ()
    spec = effect.get("spec")
    if not isinstance(spec, Mapping) or spec.get("timing") != LINKED_ABILITY_TIMING:
        return ()
    if set(effect) != ({"op", "phase", "binding_id", "spec"} | ({"cards"} if effect.get("phase") == "exile" else set())):
        return ()
    try:
        validate_linked_instruction(effect)
    except (TypeError, ValueError):
        return ()
    linked = (LINKED_EXILE_RETURN_CAPABILITY, LINKED_ABILITY_EXILE_CAPABILITY)
    if effect["phase"] == "return":
        return linked if target_schema is None else ()
    if effect.get("cards") != "$targets":
        return ()
    from .node_capability_shapes import targeted_exile_node_capabilities
    required = targeted_exile_node_capabilities(effects=({"op": "exile_permanent", "card": "$target.0"},),
        target_schema=target_schema, mechanic_ids=mechanic_ids)
    return (*required, *linked) if required else ()


def _uses_ability_link(value):
    if isinstance(value, Mapping):
        spec = value.get("spec")
        binding = value.get("binding_id")
        if value.get("op") == LINKED_EXILE_RETURN_OPERATION and (
            isinstance(binding, str) and binding.startswith(LINKED_ABILITY_BINDING_PREFIX)
            or isinstance(spec, Mapping) and (spec.get("schema_version") == 3 or spec.get("timing") == LINKED_ABILITY_TIMING)
        ):
            return True
        return any(_uses_ability_link(child) for child in value.values())
    return isinstance(value, (list, tuple)) and any(_uses_ability_link(child) for child in value)


def linked_ability_exile_runtime_dependencies(program):
    if not _uses_ability_link(program.effects):
        return ()
    effects = program.effects
    mechanics = (LINKED_EXILE_RETURN_MECHANIC, "cr-115-targets", "exile")
    if (len(effects) == 1 and effects[0].get("op") == "offer_optional_effect"
            and set(effects[0]) == {"op", "player", "effects"} and effects[0].get("player") == "$controller"
            and isinstance(effects[0]["effects"], (list, tuple))):
        effects = effects[0]["effects"]
        optional = ("effect.choice.optional_fixed",)
    else:
        optional = ()
    required = linked_ability_exile_node_capabilities(effects=effects, target_schema=program.target_schema, mechanic_ids=mechanics)
    if not required or program.active_zone != "battlefield" or program.event_condition is not None:
        raise ValueError("A linked ability requires one closed source-self instruction")
    phase = effects[0]["phase"]
    if (phase == "exile" and program.event != "permanent.enter.self"
            or phase == "return" and (program.event != "permanent.leave.self" or optional)):
        raise ValueError("The linked ability phase does not match its trigger")
    return (*required, *optional)


def linked_exile_return_node_capabilities(*, effects: Sequence[Mapping[str, Any]], target_schema: Mapping[str, Any] | None, mechanic_ids: Iterable[str]) -> tuple[str, ...]:
    if len(effects) == 1:
        return linked_ability_exile_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    if LINKED_EXILE_RETURN_MECHANIC not in set(mechanic_ids) or len(effects) != 2:
        return ()
    first, second = effects
    if set(first) != {"op", "phase", "cards", "binding_id", "spec"} or set(second) != {"op", "phase", "binding_id", "spec"}:
        return ()
    if first.get("op") != LINKED_EXILE_RETURN_OPERATION or second.get("op") != LINKED_EXILE_RETURN_OPERATION or first.get("phase") != "exile" or second.get("phase") != "return" or first.get("spec") != second.get("spec") or first.get("binding_id") != second.get("binding_id"):
        return ()
    if type(first["binding_id"]) is not str or not first["binding_id"].startswith("blink:"):
        return ()
    try:
        spec = LinkedExileReturnSpec.from_dict(first["spec"])
        if target_schema is None:
            if first["cards"] != "$source.zone_object": return ()
        else:
            group = TargetGroup.from_mapping(target_schema)
            if first["cards"] != "$targets" or group.zones != ("battlefield",) or group.categories != ("permanent",) or group.max_targets not in {1, 2, 3, 4, 5, 6, 999} or group.min_targets not in {0, group.max_targets}:
                return ()
    except (TypeError, ValueError):
        return ()
    return tuple(sorted({LINKED_EXILE_RETURN_CAPABILITY, *((CONDITIONAL_EXILE_CAPABILITY,) if spec.timing=='source_departure' else ()), *(capability for keyword in spec.keywords for capability in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])}))
