from __future__ import annotations

"""Exact capability shape for the paired linked exile/return instruction."""

from typing import Any, Iterable, Mapping, Sequence

from ..linked_exile_return_model import LINKED_EXILE_RETURN_CAPABILITY, LINKED_EXILE_RETURN_MECHANIC, LINKED_EXILE_RETURN_OPERATION, LinkedExileReturnSpec
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..targets import TargetGroup


def linked_exile_return_node_capabilities(*, effects: Sequence[Mapping[str, Any]], target_schema: Mapping[str, Any] | None, mechanic_ids: Iterable[str]) -> tuple[str, ...]:
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
    return tuple(sorted({LINKED_EXILE_RETURN_CAPABILITY, *(capability for keyword in spec.keywords for capability in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])}))
