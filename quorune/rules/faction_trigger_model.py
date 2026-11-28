"""Closed faction-branch instructions and the draw counting handoff."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..attack_batch_model import (
    DIRECT_PLAYER_ATTACK_CAPABILITY,
    DIRECT_PLAYER_ATTACK_EVENT,
    direct_player_attack_condition,
)
from ..entry_designations import ENTRY_DESIGNATION_CAPABILITY, ENTRY_FACTION_CAPABILITY, ENTRY_MARDU_JESKAI_CAPABILITY


FACTION_RESULT_FIELD = "faction_result"
FACTION_CONDITION_FIELD = "source_annotation.chosen_faction"
FACTION_DRAW_CAPABILITY = "zone.draw.opponents_committed_followup"
FACTION_RAD_CAPABILITY = "counter.producer.declared_attack_rad"
FACTION_UPKEEP_TOKEN_CAPABILITY = "trigger.condition.jeskai_upkeep_token"
FACTION_ATTACK_MULTIPLIER_CAPABILITY = "trigger.participation.mardu_attack_multiplier"
FACTION_AFTER_DRAW_ACTION = "count_faction_draw"
FACTION_DRAW_OBSERVER_FIELD = "faction_draw_observer"
FACTION_DRAW_RECEIPT_FIELD = "_faction_draw_receipt"

_BROTHERHOOD_DRAW_EFFECTS = (
    ("draw_each_player", "opponents_draw"),
    ("draw", "controller_draw"),
)


@dataclass(frozen=True, slots=True)
class CountFactionDraw:
    """Count one actually committed card draw in the originating stack frame."""

    stack_ref: str
    controller: str

    def __post_init__(self):
        if any(type(value) is not str or not value or value.startswith("$")
               for value in (self.stack_ref, self.controller)):
            raise ValueError("A faction draw observer requires its resolved stack and controller")

    def to_dict(self):
        return {"action": FACTION_AFTER_DRAW_ACTION, "stack_ref": self.stack_ref, "controller": self.controller}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"action", "stack_ref", "controller"}
                or value["action"] != FACTION_AFTER_DRAW_ACTION):
            raise ValueError("A faction draw observer requires exact typed fields")
        return cls(value["stack_ref"], value["controller"])


def faction_event(choice):
    if choice in {"Brotherhood", "Jeskai"}:
        return "step.begin"
    if choice == "Enclave":
        return DIRECT_PLAYER_ATTACK_EVENT
    raise ValueError("Unknown source faction")


def faction_condition(choice):
    faction = {"field": FACTION_CONDITION_FIELD, "op": "eq", "value": choice}
    if choice in {"Brotherhood", "Jeskai"}:
        return {"all": [
            {"field": "player", "op": "eq", "value": "$source.controller"},
            {"field": "step", "op": "eq", "value": "upkeep"},
            faction,
        ]}
    if choice == "Enclave":
        return {"all": [direct_player_attack_condition("opponent_attacks_controller"), faction]}
    raise ValueError("Unknown source faction")


def faction_effects(choice):
    if choice == "Jeskai":
        from ..fixed_token_production import temporary_haste_lifelink_goblin_effect

        return (temporary_haste_lifelink_goblin_effect(),)
    if choice == "Brotherhood":
        return tuple({"op": op, "schema_version": 1, FACTION_RESULT_FIELD: role} for op, role in _BROTHERHOOD_DRAW_EFFECTS)
    if choice == "Enclave":
        return ({"op": "place_player_counters", "schema_version": 1, FACTION_RESULT_FIELD: "attack_rad"},)
    raise ValueError("Unknown source faction")


def validate_faction_instruction(effect):
    if not isinstance(effect, Mapping) or type(effect.get("schema_version")) is not int:
        raise ValueError("A faction result uses its exact versioned instruction")
    for choice in ("Brotherhood", "Enclave"):
        for expected in faction_effects(choice):
            if dict(effect) == expected:
                return choice, expected[FACTION_RESULT_FIELD]
    raise ValueError("A faction result does not match one of the compiled phases")


def faction_branch_capabilities(choice):
    if choice == "Jeskai":
        from .token_creation_capability_shapes import created_token_haste_closure

        token, = faction_effects(choice)
        return (ENTRY_DESIGNATION_CAPABILITY, ENTRY_MARDU_JESKAI_CAPABILITY,
                FACTION_UPKEEP_TOKEN_CAPABILITY, "trigger.effect.fixed_event", "trigger.placement.apnap",
                *created_token_haste_closure(token)[0])
    common = (ENTRY_DESIGNATION_CAPABILITY, ENTRY_FACTION_CAPABILITY, "trigger.placement.apnap")
    if choice == "Brotherhood":
        return (*common, FACTION_DRAW_CAPABILITY, "zone.draw.library_to_hand", "trigger.effect.fixed_event")
    if choice == "Enclave":
        return (*common, FACTION_RAD_CAPABILITY, DIRECT_PLAYER_ATTACK_CAPABILITY,
                "counter.producer.fixed_player_effect", "counter.placement.quantity_replacement",
                "trigger.effect.fixed_event")
    raise ValueError("Unknown source faction")
