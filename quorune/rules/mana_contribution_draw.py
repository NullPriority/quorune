from __future__ import annotations

"""Closed instruction and ordered payment journal for a shared draw."""

from collections.abc import Mapping
from dataclasses import dataclass


MANA_CONTRIBUTION_DRAW_OPERATION = "mana_contribution_draw"
MANA_CONTRIBUTION_DRAW_MECHANIC = "mana-contribution-draw"
MANA_CONTRIBUTION_DRAW_CAPABILITY = "draw.mana_contribution"
MANA_CONTRIBUTION_DRAW_CAPABILITIES = (
    MANA_CONTRIBUTION_DRAW_CAPABILITY,
    "zone.draw.library_to_hand",
)

_INSTRUCTION_FIELDS = frozenset({"op", "schema_version", "player"})


def validate_mana_contribution_draw(effect: Mapping, *, resolved: bool = False) -> None:
    if (not isinstance(effect, Mapping) or set(effect) != _INSTRUCTION_FIELDS
            or effect["op"] != MANA_CONTRIBUTION_DRAW_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1):
        raise ValueError("Mana contribution draw requires its closed version-one instruction")
    player = effect["player"]
    if resolved:
        if type(player) is not str or not player or player.startswith("$"):
            raise ValueError("Mana contribution draw requires the resolving controller")
    elif player != "$controller":
        raise ValueError("Mana contribution draw starts with the resolving controller")


def mana_contribution_draw_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 1
            or MANA_CONTRIBUTION_DRAW_MECHANIC not in mechanic_ids):
        return ()
    try:
        validate_mana_contribution_draw(effects[0])
    except (TypeError, ValueError):
        return ()
    return MANA_CONTRIBUTION_DRAW_CAPABILITIES


@dataclass(frozen=True, slots=True)
class ManaContribution:
    player: str
    amount: int

    def __post_init__(self) -> None:
        if (type(self.player) is not str or not self.player or self.player.startswith("$")
                or type(self.amount) is not int or self.amount < 0):
            raise ValueError("A contribution requires a paying player and a nonnegative mana amount")

    @classmethod
    def from_dict(cls, value: Mapping) -> ManaContribution:
        if not isinstance(value, Mapping) or set(value) != {"player", "amount"}:
            raise ValueError("A mana contribution has closed player and amount fields")
        return cls(value["player"], value["amount"])

    def to_dict(self) -> dict:
        return {"player": self.player, "amount": self.amount}
