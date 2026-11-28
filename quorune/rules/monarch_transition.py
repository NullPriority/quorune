"""A committed change of monarch can trigger a controller-owned player choice."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap


MONARCH_BECAME_EVENT = "monarch.became"
MONARCH_BECAME_CAPABILITY = "trigger.event.normalized_monarch_change"
MONARCH_BECAME_TEMPLATE = "controller-became-monarch-player-sacrifice-v1"
MONARCH_BECAME_VARIANT = "controller_became_monarch"
MONARCH_CONTROLLER_CONDITION = FrozenMap({"field": "player", "op": "eq", "value": "$source.controller"})


def dispatch_committed_monarch_transition(host, player, previous):
    if not host.semantics.programs_for_event(MONARCH_BECAME_EVENT):
        return
    if player == previous or host.state.monarch != player or player not in host.active_seats:
        raise GameRuleError("A monarch transition requires its newly committed active player")
    if previous is not None and previous not in host.state.players:
        raise GameRuleError("A monarch transition has an unknown previous player")
    host._dispatch_semantic_event(MONARCH_BECAME_EVENT, {"player": player, "previous": previous})


def monarch_became_runtime_dependencies(program):
    if program.event != MONARCH_BECAME_EVENT and MONARCH_BECAME_CAPABILITY not in program.capability_dependencies:
        return ()
    from ..compiler.affected_player_sacrifice_templates import (
        FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY, fixed_affected_player_sacrifice_effect_template,
    )

    template = fixed_affected_player_sacrifice_effect_template("Target player sacrifices a creature of their choice.")
    if ((program.event, program.active_zone) != (MONARCH_BECAME_EVENT, "battlefield")
            or not program.ability_id.startswith("trigger:") or program.cost_schema is not None
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage or program.handlers or FrozenMap(program.event_condition or {}) != MONARCH_CONTROLLER_CONDITION
            or program.target_schema is None or FrozenMap(program.target_schema) != FrozenMap(template.target_schema)
            or type(program.target_schema["count"]) is not int
            or len(program.effects) != 1 or FrozenMap(program.effects[0]) != FrozenMap(template.effects[0])
            or type(program.effects[0]["count"]) is not int):
        raise ValueError("A monarch-triggered sacrifice requires the committed controller and one ordinary player target")
    return (MONARCH_BECAME_CAPABILITY, FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
            "trigger.placement.apnap", "target.revalidate_resolution", "zone.change.destination_replacement")
