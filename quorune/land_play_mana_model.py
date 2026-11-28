from __future__ import annotations

"""A fixed output replaced by one other fixed output after an actual land play."""

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .fixed_mana_abilities import FixedActivatedManaAbilitySpec, FixedManaMode, MANA_KEYS
from .replacement.immutable import FrozenMap


LAND_PLAY_MANA_KIND = "land_play_this_turn_mana"
LAND_PLAY_MANA_CAPABILITY = "mana.production.current_turn_land_play"
LAND_PLAY_MANA_HANDLER_ID = "ability.activated.mana.land-play-output.v1"
LAND_PLAY_MANA_RULES = ("106.7", "109.5", "305.1", "305.2", "305.4", "605.1a", "605.3a", "605.3b")


@dataclass(frozen=True, slots=True)
class LandPlayManaOutput:
    normal_color: str
    played_color: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.normal_color) is not str or self.normal_color not in MANA_KEYS
                or type(self.played_color) is not str or self.played_color not in MANA_KEYS
                or self.normal_color == self.played_color):
            raise ValueError("Land-play mana requires two distinct canonical single-mana colors")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": LAND_PLAY_MANA_KIND, "schema_version": self.schema_version,
                "normal_color": self.normal_color, "played_color": self.played_color}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LandPlayManaOutput":
        if (not isinstance(data, Mapping)
                or set(data) != {"kind", "schema_version", "normal_color", "played_color"}
                or data["kind"] != LAND_PLAY_MANA_KIND):
            raise ValueError("Land-play mana output requires its complete exact descriptor")
        return cls(data["normal_color"], data["played_color"], data["schema_version"])


def land_play_mana_output(ability) -> LandPlayManaOutput | None:
    value = getattr(ability, "dynamic_mana_output", None)
    if isinstance(value, Mapping) and value.get("kind") == LAND_PLAY_MANA_KIND:
        return LandPlayManaOutput.from_dict(value)
    return None


def validate_land_play_mana_ability(ability) -> None:
    if (
        ability.mana_ability is not True or ability.tap_source is not True
        or ability.zones != ("battlefield",) or ability.target_schema is not None
        or ability.loyalty_delta is not None
        or any(ability.mana.values())
        or any((ability.untap_source, ability.sacrifice_source, ability.exile_source, ability.discard_source,
                ability.life_payment, ability.energy_payment, ability.random_discard_payment,
                ability.complex_symbols, ability.mana_cost_options, ability.choices, ability.uncompiled_costs,
                ability.activation_conditions, ability.activation_limit, ability.sorcery_speed,
                ability.fixed_mana_outputs, ability.color_set_mana_output, ability.fixed_mana_result,
                ability.source_counter_removal_cost, ability.mana_spend_restriction,
                ability.generic_reduction_per_legendary_creature, ability.waterbend, ability.power_up,
                ability.announced_x_symbols, ability.forecast, ability.reconfigure, ability.boast,
                ability.mill_library_cost, ability.granting_attachment_payment, ability.source_counter_placement_cost,
                ability.crew_threshold is not None, ability.saddle_threshold is not None))
    ):
        raise ValueError("Land-play mana requires the closed tap-only battlefield activation")


@dataclass(frozen=True, slots=True)
class LandPlayActivatedManaSpec:
    costs: FixedActivatedManaAbilitySpec
    output: LandPlayManaOutput

    def __post_init__(self) -> None:
        if (not isinstance(self.costs, FixedActivatedManaAbilitySpec)
                or not isinstance(self.output, LandPlayManaOutput)
                or self.costs.modes != (FixedManaMode(green=1),)):
            raise ValueError("Land-play mana requires one closed cost and conditional output")
        self.to_activated_ability()

    def to_dict(self) -> dict[str, Any]:
        return {"costs": self.costs.to_dict(), "output": self.output.to_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LandPlayActivatedManaSpec":
        if not isinstance(data, Mapping) or set(data) != {"costs", "output"}:
            raise ValueError("Land-play activated mana uses a closed cost and output shape")
        return cls(FixedActivatedManaAbilitySpec.from_dict(data["costs"]), LandPlayManaOutput.from_dict(data["output"]))

    def to_activated_ability(self):
        return replace(self.costs.to_activated_ability(), fixed_mana_outputs=(),
                       dynamic_mana_output=FrozenMap(self.output.to_dict()))
