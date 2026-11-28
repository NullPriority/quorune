"""A named opponent's committed off-turn draw and tapped Treasure result."""

from collections.abc import Mapping

from ..fixed_token_production import predefined_token_identity
from ..replacement.immutable import FrozenMap
from ..standard_token_abilities import TOKEN_ABILITY_PROFILE_FIELD


OFF_TURN_DRAW_FIELD = "draw_recipient_is_not_active_player"
OFF_TURN_DRAW_CAPABILITY = "trigger.condition.opponent_off_turn_draw"
OFF_TURN_DRAW_COVERAGE = "fixed_opponent_off_turn_draw"


def off_turn_opponent_draw_condition():
    return {"all": [
        {"field": "player", "op": "ne", "value": "$source.controller"},
        {"field": OFF_TURN_DRAW_FIELD, "op": "truthy", "value": True},
    ]}


def tapped_treasure_effect():
    name, characteristics = predefined_token_identity("Treasure", {
        "type_line": "Token Artifact — Treasure",
        "display_text": "{T}, Sacrifice this token: Add one mana of any color.",
        TOKEN_ABILITY_PROFILE_FIELD: "tap_sac_any_color_mana_v1",
    })
    return {"op": "create_token", "controller": "$controller", "name": name,
            "quantity": 1, "tapped": True, "characteristics": characteristics}


def opponent_off_turn_draw_dependencies(program):
    condition = program.event_condition
    # The atom is recognized independently of the enclosing condition and declared capability.
    def uses_field(value):
        if isinstance(value, Mapping):
            return value.get("field") == OFF_TURN_DRAW_FIELD or any(uses_field(child) for child in value.values())
        return isinstance(value, (tuple, list)) and any(uses_field(child) for child in value)

    if not uses_field(condition) and OFF_TURN_DRAW_CAPABILITY not in program.capability_dependencies:
        return ()
    expected = off_turn_opponent_draw_condition()
    if ((program.event, program.active_zone) != ("card.drawn", "battlefield")
            or not isinstance(condition, Mapping) or FrozenMap(condition) != FrozenMap(expected)
            or condition["all"][1]["value"] is not True
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or not {OFF_TURN_DRAW_COVERAGE, "intervening_condition"} <= set(program.coverage)
            or len(program.effects) != 1 or FrozenMap(program.effects[0]) != FrozenMap(tapped_treasure_effect())
            or type(program.effects[0].get("quantity")) is not int or program.effects[0].get("tapped") is not True):
        raise ValueError("An off-turn draw requires its closed opponent occurrence and tapped Treasure")
    return (OFF_TURN_DRAW_CAPABILITY, "trigger.event.normalized_card_draw", "trigger.placement.apnap",
            "trigger.effect.fixed_event", "token.creation.fixed_definition")


def off_turn_draw_holds(state, context):
    drawer = context.get("player")
    active = state.active_player
    ordinal = context.get("draw_ordinal")
    if (type(drawer) is not str or drawer not in state.players
            or type(active) is not str or active not in state.players
            or type(ordinal) is not int or ordinal < 1):
        raise ValueError("An off-turn draw requires its actual committed drawer and active player")
    return drawer != active
