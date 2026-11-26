"""Closed effect shape for conditional ordinary play from private library tops."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..creature_subtypes import canonical_creature_subtype


CONTROLLER_PRIVATE_TOP_EXILE_OPERATION = "exile_controller_library_tops_private_play"
CONTROLLER_PRIVATE_TOP_EXILE_CAPABILITY = "library.exile.controller_top_private_conditional_play"
CONTROLLER_PRIVATE_TOP_EXILE_MECHANIC = "controller-private-top-conditional-play"
CONTROLLER_PRIVATE_TOP_EXILE_RULES = ("305.1", "400.7", "406.3", "406.3a", "601.2", "608.2c", "609.3")


@dataclass(frozen=True, slots=True)
class ControllerPrivateTopExile:
    """One fixed count and a currently controlled permanent's creature subtype."""

    subtype: str

    def __post_init__(self):
        if type(self.subtype) is not str or canonical_creature_subtype(self.subtype) != self.subtype:
            raise ValueError("Private top exile requires a canonical creature subtype")

    def instruction(self):
        return {"op": CONTROLLER_PRIVATE_TOP_EXILE_OPERATION, "schema_version": 1,
                "player": "$controller", "count": 2, "subtype": self.subtype}

    @classmethod
    def from_instruction(cls, effect, *, actor=None):
        if (not isinstance(effect, Mapping)
                or set(effect) != {"op", "schema_version", "player", "count", "subtype"}
                or effect["op"] != CONTROLLER_PRIVATE_TOP_EXILE_OPERATION
                or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
                or type(effect["count"]) is not int or effect["count"] != 2
                or effect["player"] != ("$controller" if actor is None else actor)):
            raise ValueError("Private top exile requires its closed controller-library instruction")
        return cls(effect["subtype"])


def _private_top_exile_instructions(value):
    if isinstance(value, Mapping):
        if value.get("op") == CONTROLLER_PRIVATE_TOP_EXILE_OPERATION:
            yield value
        for child in value.values():
            yield from _private_top_exile_instructions(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _private_top_exile_instructions(child)


def controller_private_top_exile_program_dependencies(program, values):
    """Require the closed private batch to be the whole untargeted spell."""
    instructions = tuple(_private_top_exile_instructions(values))
    if not instructions:
        return ()
    if (program.event != "resolve" or not program.ability_id.startswith("spell:")
            or program.target_schema is not None or len(instructions) != 1
            or tuple(program.effects) != instructions):
        raise ValueError("Private controller-library exile requires one untargeted spell instruction")
    ControllerPrivateTopExile.from_instruction(instructions[0])
    return (CONTROLLER_PRIVATE_TOP_EXILE_CAPABILITY,)


def is_closed_controller_private_top_exile_program(program):
    try:
        required = controller_private_top_exile_program_dependencies(program, program.effects)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
