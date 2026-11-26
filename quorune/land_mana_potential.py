from __future__ import annotations

"""The closed tap ability that mirrors the mana potential of a group of lands."""

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping

from .replacement.immutable import FrozenMap

LAND_MANA_POTENTIAL_KIND = "land_mana_potential"
LAND_MANA_POTENTIAL_CAPABILITY = "mana.production.land_potential"
LAND_MANA_POTENTIAL_SOURCE_COUNTER_CAPABILITY = "mana.production.land_potential_source_counter"
LAND_MANA_POTENTIAL_HANDLER_ID = "ability.activated.mana.land-potential.v1"
LAND_MANA_POTENTIAL_RULES = (
    "106.1a", "106.1b", "106.6", "106.7", "106.12a", "602.2b", "605.1a", "605.3b", "707.2",
)


class LandManaPotential(str, Enum):
    OPPONENT_COLORS = "opponent_colors"
    CONTROLLED_TYPES = "controlled_types"
    CONTROLLED_TYPES_WITH_PLUS_ONE_COUNTER = "controlled_types_with_plus_one_counter"

    @property
    def capabilities(self) -> tuple[str, ...]:
        if self is LandManaPotential.CONTROLLED_TYPES_WITH_PLUS_ONE_COUNTER:
            return (LAND_MANA_POTENTIAL_CAPABILITY, LAND_MANA_POTENTIAL_SOURCE_COUNTER_CAPABILITY)
        return (LAND_MANA_POTENTIAL_CAPABILITY,)

    def amount(self, counters: Mapping[str, int]) -> int:
        if self is LandManaPotential.CONTROLLED_TYPES_WITH_PLUS_ONE_COUNTER and counters.get("+1/+1", 0) > 0:
            return 3
        return 1

    @property
    def types(self) -> str:
        return "WUBRG" if self is LandManaPotential.OPPONENT_COLORS else "WUBRGC"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": LAND_MANA_POTENTIAL_KIND, "schema_version": 1, "scope": self.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LandManaPotential:
        if (not isinstance(value, Mapping) or set(value) != {"kind", "schema_version", "scope"}
                or value["kind"] != LAND_MANA_POTENTIAL_KIND
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Land mana potential has an invalid descriptor")
        return cls(value["scope"])


_EFFECT_TEXT = {
    LandManaPotential.OPPONENT_COLORS:
        "Add one mana of any color that a land an opponent controls could produce.",
    LandManaPotential.CONTROLLED_TYPES:
        "Add one mana of any type that a land you control could produce.",
    LandManaPotential.CONTROLLED_TYPES_WITH_PLUS_ONE_COUNTER:
        "Add one mana of any type that a land you control could produce. "
        "If this creature has a +1/+1 counter on it, add three mana of that type instead.",
}
_SCOPE_FROM_EFFECT = {text.casefold(): scope for scope, text in _EFFECT_TEXT.items()}


@dataclass(frozen=True, slots=True)
class LandPotentialActivatedManaSpec:
    line_index: int
    output: LandManaPotential

    def __post_init__(self):
        if type(self.line_index) is not int or self.line_index < 0 or not isinstance(self.output, LandManaPotential):
            raise ValueError("Land mana potential requires a pinned activation line and supported scope")

    def to_dict(self) -> dict[str, Any]:
        return {"line_index": self.line_index, "output": self.output.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LandPotentialActivatedManaSpec:
        if not isinstance(value, Mapping) or set(value) != {"line_index", "output"}:
            raise ValueError("Land mana potential activation fields are invalid")
        return cls(value["line_index"], LandManaPotential.from_dict(value["output"]))

    def _tap_ability(self, *, dynamic_output):
        from .abilities import ActivatedAbility

        effect = _EFFECT_TEXT[self.output]
        return ActivatedAbility(
            ability_id=f"ab{self.line_index + 1}", line_index=self.line_index,
            oracle_line="{T}: " + effect, cost_text="{T}", effect_text=effect,
            zones=("battlefield",), mana={}, tap_source=True, mana_ability=True,
            dynamic_mana_output=dynamic_output,
        )

    def to_activated_ability(self):
        return self._tap_ability(dynamic_output=FrozenMap(self.output.to_dict()))

    def matches_tap_ability(self, ability) -> bool:
        expected = self._tap_ability(dynamic_output=None)
        return replace(ability, dynamic_mana_output=None, builtin_semantic_key=None) == expected


def land_mana_potential_output(ability) -> LandManaPotential | None:
    raw = ability.dynamic_mana_output
    if isinstance(raw, Mapping) and raw.get("kind") == LAND_MANA_POTENTIAL_KIND:
        return LandManaPotential.from_dict(raw)
    return None


def validate_land_potential_ability(ability, output: LandManaPotential) -> None:
    if not LandPotentialActivatedManaSpec(ability.line_index, output).matches_tap_ability(ability):
        raise ValueError("Land mana potential requires its exact battlefield tap only ability")


def compile_land_potential_mana(ability) -> LandPotentialActivatedManaSpec | None:
    output = _SCOPE_FROM_EFFECT.get(" ".join(ability.effect_text.casefold().split()))
    if output is None:
        return None
    spec = LandPotentialActivatedManaSpec(ability.line_index, output)
    return spec if spec.matches_tap_ability(ability) else None
