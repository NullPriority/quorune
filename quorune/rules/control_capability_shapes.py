from __future__ import annotations

"""Closed ownership for resolution-created control instructions."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.fixed_control_templates import FIXED_CONTROL_CAPABILITY, FIXED_CONTROL_MECHANIC
from ..continuous_effect_model import ContinuousEffectDuration
from ..control_effects import control_set_query_is_closed
from ..object_predicate import ObjectQuerySpec
from .permanent_predicate_capability_shapes import (
    direct_permanent_target_schema_is_closed, direct_target_predicate_capabilities,
)


def fixed_control_node_capabilities(
    *, effects: Sequence[Mapping[str, Any]], target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = set(mechanic_ids)
    if not {FIXED_CONTROL_MECHANIC, "cr-611-continuous-effects"}.issubset(mechanics) or not effects:
        return ()
    dependencies = {FIXED_CONTROL_CAPABILITY}
    if target_schema is None:
        if len(effects) != 1:
            return ()
        effect = effects[0]
        if set(effect) != {"op", "predicate", "controller", "duration", "source", "steps"}:
            return ()
        if effect["op"] != "gain_control_set" or effect["controller"] != "$controller" or effect["source"] != "$source":
            return ()
        try:
            query = ObjectQuerySpec.from_dict(effect["predicate"])
            duration = ContinuousEffectDuration(effect["duration"])
        except (TypeError, ValueError):
            return ()
        if duration not in {duration.UNTIL_END_OF_TURN, duration.ZONE_OBJECT}:
            return ()
        steps = effect["steps"]
        if not isinstance(steps, (tuple, list)) or tuple(steps) not in {
            ("gain_control",), ("untap", "gain_control"), ("gain_control", "untap"),
            ("gain_control", "haste"), ("untap", "gain_control", "haste"),
            ("gain_control", "untap", "haste"),
        } or not control_set_query_is_closed(query, actor="$controller"):
            return ()
        if "untap" in steps:
            if "tap-and-untap" not in mechanics:
                return ()
            dependencies.add("permanent.untap.effect")
        if "haste" in steps:
            if "haste" not in mechanics or duration is not duration.UNTIL_END_OF_TURN:
                return ()
            dependencies.add("continuous.resolution.fixed_characteristics_until_end_of_turn")
        return tuple(sorted(dependencies))
    if "cr-115-targets" not in mechanics or not direct_permanent_target_schema_is_closed(target_schema):
        return ()
    if len(effects) == 2:
        if dict(effects[0]) != {"op": "untap", "card": "$target.0"} or "tap-and-untap" not in mechanics:
            return ()
        dependencies.add("permanent.untap.effect")
    elif len(effects) != 1:
        return ()
    effect = effects[-1]
    try:
        duration = ContinuousEffectDuration(effect.get("duration"))
    except (TypeError, ValueError):
        return ()
    expected = {"op", "card", "controller", "duration", "source"}
    if duration.source_bound:
        expected.add("duration_source")
        if effect.get("duration_source") != "$source.zone_object" or len(effects) != 1:
            return ()
    elif duration not in {duration.UNTIL_END_OF_TURN, duration.ZONE_OBJECT}:
        return ()
    if (set(effect) != expected or effect.get("op") != "gain_control"
            or effect.get("card") != "$target.0" or effect.get("controller") != "$controller"
            or effect.get("source") != "$source"):
        return ()
    dependencies.add("target.revalidate_resolution")
    dependencies.update(direct_target_predicate_capabilities(target_schema))
    return tuple(sorted(dependencies))
