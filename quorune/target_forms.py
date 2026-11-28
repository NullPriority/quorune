from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

EVENT_PLAYER_TARGET_REFERENCES = frozenset({
    '$context.defending_player', '$context.target', '$context.player',
})


def _values(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        type(item) is not str or not item.strip() for item in value
    ):
        raise ValueError(
            f"Target characteristic form {field_name} must be an array of "
            "nonempty strings"
        )
    normalized = tuple(sorted(item.strip().casefold() for item in value))
    if len(normalized) != len(set(normalized)):
        raise ValueError(
            f"Target characteristic form {field_name} must be unique"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class TargetCharacteristicForm:
    """One closed type/subtype/supertype alternative in a target query."""

    types_all: tuple[str, ...] = ()
    subtypes_any: tuple[str, ...] = ()
    supertypes_any: tuple[str, ...] = ()
    supertypes_none: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("types_all", "subtypes_any", "supertypes_any", "supertypes_none"):
            object.__setattr__(
                self,
                field_name,
                _values(getattr(self, field_name), field_name=field_name),
            )
        if self.supertypes_none and (
            self.supertypes_none != ("basic",) or self.types_all != ("land",)
            or self.subtypes_any or self.supertypes_any
        ):
            raise ValueError("The excluded-supertype alternative must be a nonbasic land")
        if not (self.types_all or self.subtypes_any or self.supertypes_any):
            raise ValueError(
                "Target characteristic forms must constrain a subject"
            )

    def matches(
        self,
        *,
        types: set[str],
        subtypes: set[str],
        supertypes: set[str],
    ) -> bool:
        return bool(
            set(self.types_all).issubset(types)
            and (
                not self.subtypes_any
                or set(self.subtypes_any).intersection(subtypes)
            )
            and (
                not self.supertypes_any
                or set(self.supertypes_any).intersection(supertypes)
            )
            and not set(self.supertypes_none).intersection(supertypes)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "types_all": list(self.types_all),
            "subtypes_any": list(self.subtypes_any),
            "supertypes_any": list(self.supertypes_any),
            **({"supertypes_none": list(self.supertypes_none)} if self.supertypes_none else {}),
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "TargetCharacteristicForm":
        expected = {"types_all", "subtypes_any", "supertypes_any"}
        if not isinstance(value, Mapping) or set(value) not in (expected, expected | {"supertypes_none"}):
            raise ValueError(
                "Target characteristic forms require exact typed fields"
            )
        excluded = _values(value.get("supertypes_none", ()), field_name="supertypes_none")
        if "supertypes_none" in value and not excluded:
            raise ValueError("An excluded-supertype alternative must name the excluded supertype")
        return cls(
            types_all=_values(value["types_all"], field_name="types_all"),
            subtypes_any=_values(
                value["subtypes_any"], field_name="subtypes_any"
            ),
            supertypes_any=_values(
                value["supertypes_any"], field_name="supertypes_any"
            ),
            supertypes_none=excluded,
        )


HISTORIC_TARGET_FORMS = (
    TargetCharacteristicForm(types_all=("artifact",)),
    TargetCharacteristicForm(supertypes_any=("legendary",)),
    TargetCharacteristicForm(subtypes_any=("saga",)),
)


__all__ = ["HISTORIC_TARGET_FORMS", "TargetCharacteristicForm"]


SHARED_PERMANENT_TYPE_PAIR_FIELD = "shared_permanent_type_pair"
SHARED_PERMANENT_TYPE_PAIR_CAPABILITY = "target.relation.shared_permanent_card_type"


class SharedPermanentTypeScope(StrEnum):
    PERMANENT = "permanent"
    ARTIFACT_OR_CREATURE = "artifact_or_creature"

    @property
    def types(self):
        if self is self.ARTIFACT_OR_CREATURE:
            return frozenset(("artifact", "creature"))
        return frozenset(("artifact", "battle", "creature", "enchantment", "land", "planeswalker"))


@dataclass(frozen=True, slots=True)
class SharedPermanentTypePair:
    left: str
    right: str
    scope: SharedPermanentTypeScope

    def __post_init__(self):
        if (type(self.left) is not str or not self.left or type(self.right) is not str
                or not self.right or self.left == self.right):
            raise ValueError("A shared permanent type pair requires two distinct target groups")
        if not isinstance(self.scope, SharedPermanentTypeScope):
            raise ValueError("A shared permanent type pair requires a represented card-type scope")

    def to_dict(self):
        return {"schema_version": 1, "left": self.left, "right": self.right, "scope": self.scope.value}

    @classmethod
    def from_mapping(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "left", "right", "scope"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Shared permanent type pairs require their exact versioned fields")
        return cls(value["left"], value["right"], SharedPermanentTypeScope(value["scope"]))
