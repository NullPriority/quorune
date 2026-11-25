"""A source-bound indefinite defender removal with optional creature types."""

from collections.abc import Mapping
from dataclasses import dataclass

from .creature_subtypes import canonical_creature_subtype
from .continuous_effect_model import ContinuousOperation, Layer


SOURCE_BLOCK_DEFENDER_VERSION = 26
SOURCE_BLOCK_DEFENDER_FIELD = "characteristics"
SOURCE_BLOCK_DEFENDER_MECHANIC = "source-block-defender-removal"
SOURCE_BLOCK_DEFENDER_CAPABILITY = "continuous.resolution.source_block_defender_removal"


@dataclass(frozen=True, slots=True)
class SourceBlockDefenderSpec:
    creature_subtypes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        values = self.creature_subtypes
        if (
            type(values) is not tuple or len(values) not in {0, 2}
            or any(type(value) is not str or canonical_creature_subtype(value) != value for value in values)
            or values != tuple(sorted(set(values)))
        ):
            raise ValueError("A blocking source uses zero or two distinct canonical creature types")

    def to_dict(self) -> dict[str, list[str]]:
        return {"creature_subtypes": list(self.creature_subtypes)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "SourceBlockDefenderSpec":
        if not isinstance(raw, Mapping) or set(raw) != {"creature_subtypes"}:
            raise ValueError("The source block characteristic fields are closed")
        values = raw["creature_subtypes"]
        if not isinstance(values, (list, tuple)):
            raise ValueError("The source block creature types require an array")
        return cls(tuple(values))

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        return SOURCE_BLOCK_DEFENDER_CAPABILITY, "combat.attack.defender"

    def layer_operations(self):
        removal = (Layer.ABILITY, "6", (ContinuousOperation("remove_ability", "Defender"),))
        if not self.creature_subtypes:
            return (removal,)
        replace_types = ContinuousOperation("set_creature_subtypes", self.creature_subtypes)
        return (Layer.TYPE, "4", (replace_types,)), removal
