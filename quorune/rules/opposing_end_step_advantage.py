"""Current count comparisons locked to an opposing end step and trigger controller."""

from __future__ import annotations

from collections.abc import Mapping

from ..continuous_effect_model import Layer
from ..player_maximum import current_player_metric_values
from ..player_maximum_model import PlayerMaximumSpec
from ..replacement.immutable import FrozenMap

CAPABILITY = "trigger.condition.opposing_end_step_permanent_advantage"
COVERAGE = "opposing_end_step_permanent_advantage"
FIELD = COVERAGE + "_v1"
DEPENDENCIES = (
    CAPABILITY, "trigger.condition.fixed_public_state", "trigger.effect.fixed_event", "trigger.placement.apnap",
)


def opposing_end_step_advantage_condition(metric):
    if metric not in ("creature_count", "land_count"):
        raise ValueError("An opposing end step requires a closed battlefield comparison metric")
    return {"all": [
        {"field": "step", "op": "eq", "value": "end_step"},
        {"field": "player", "op": "ne", "value": "$source.controller"},
        {"field": FIELD, "op": "eq", "value": metric},
    ]}


def uses_opposing_end_step_advantage(value):
    if isinstance(value, Mapping):
        return value.get("field") == FIELD or any(uses_opposing_end_step_advantage(child) for child in value.values())
    return isinstance(value, (tuple, list)) and any(uses_opposing_end_step_advantage(child) for child in value)


def _metric_from_leaf(condition):
    if not isinstance(condition, Mapping):
        raise ValueError("Opposing end-step comparison requires a closed leaf")
    metric = condition.get("value")
    if (metric not in ("creature_count", "land_count")
            or condition != {"field": FIELD, "op": "eq", "value": metric}):
        raise ValueError("Invalid opposing end-step comparison leaf")
    return metric


def opposing_end_step_advantage_value(host, condition, context, controller):
    metric = _metric_from_leaf(condition)
    participant = context.get("player")
    if (context.get("step") != "end_step" or type(participant) is not str or participant not in host.state.players
            or type(controller) is not str or controller not in host.state.players):
        raise ValueError("Count advantage requires the original end-step player and trigger controller")
    if participant == controller:
        return None
    counts = dict(current_player_metric_values(
        host.state, PlayerMaximumSpec(metric),
        public_object_resolver=lambda card: host._public_object_query_result(card, _maximum_layer=Layer.COLOR),
    ))
    if participant in counts and controller in counts and counts[participant] > counts[controller]:
        return metric
    return None


def _program_metric(program):
    condition = FrozenMap(program.event_condition or {})
    leaves = condition.get("all", ())
    if not isinstance(leaves, (list, tuple)) or len(leaves) != 3:
        raise ValueError("Count advantage requires the single original opposing-end-step occurrence")
    metric = _metric_from_leaf(leaves[2])
    if condition != FrozenMap(opposing_end_step_advantage_condition(metric)):
        raise ValueError("Count advantage changed its opposing-end-step occurrence")
    return metric


def _optional_tapped_basic_search(effect):
    children = effect.get("effects")
    if (set(effect) != {"op", "player", "effects"} or effect.get("player") != "$controller"
            or not isinstance(children, (list, tuple)) or len(children) != 1
            or not isinstance(children[0], Mapping)):
        return False
    child = children[0]
    selector = child.get("selector")
    if not isinstance(selector, Mapping):
        return False
    subtypes = selector.get("subtypes_any")
    return (
        isinstance(subtypes, (list, tuple)) and len(subtypes) == 1 and subtypes[0] in ("plains", "island", "swamp", "mountain", "forest")
        and FrozenMap(child) == FrozenMap({
            "op": "search", "zone": "library", "destination": "battlefield",
            "selector": {"types": ["land"], "supertypes": ["basic"], "subtypes_any": subtypes},
            "count": {"minimum": 1, "maximum": 1}, "enters_tapped_override": True, "shuffle_after": True,
        })
    )


def opposing_end_step_runtime_dependencies(program):
    present = (uses_opposing_end_step_advantage(program.event_condition)
               or CAPABILITY in program.capability_dependencies or COVERAGE in program.coverage)
    if not present:
        return ()
    metric = _program_metric(program)
    required_op = "create_token" if metric == "creature_count" else "offer_optional_effect"
    if ((program.event, program.active_zone) != ("step.begin", "battlefield")
            or not {"intervening_condition", COVERAGE} <= set(program.coverage)
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or len(program.effects) != 1 or program.effects[0].get("op") != required_op):
        raise ValueError("Opposing-end-step advantage requires its closed nontargeted result and current recheck")
    if metric == "land_count" and not _optional_tapped_basic_search(program.effects[0]):
        raise ValueError("Land comparison requires the original optional tapped basic-typed search")
    return DEPENDENCIES


def recheck_opposing_end_step_advantage(host, program, context, controller):
    metric = _program_metric(program)
    if context.get("event") != "step.begin" or context.get("intervening_condition_controller") != controller:
        raise ValueError("Count advantage lost its original occurrence or trigger controller")
    condition = opposing_end_step_advantage_condition(metric)["all"][2]
    return opposing_end_step_advantage_value(host, condition, context, controller) == metric
