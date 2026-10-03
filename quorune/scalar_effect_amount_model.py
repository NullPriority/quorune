from __future__ import annotations

"""Closed scalar producers consumed by the existing result-amount boundary."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .query_effect_amount_model import PublicQueryAmountError

SCALAR_AMOUNT_KIND = "scalar_effect_amount"
SCALAR_AMOUNT_CAPABILITY = "quantity_expression.scalar_effect_amount"
SCALAR_AMOUNT_MECHANIC = "scalar-effect-amount"
SCALAR_REFERENCE_CONTEXT = "scalar_reference_snapshots"


class ScalarAmountOrigin(StrEnum):
    SOURCE = "source_characteristic"
    TARGET = "target_characteristic"
    EVENT_OBJECT = "event_object_characteristic"
    EVENT_AMOUNT = "committed_event_amount"
    HISTORY = "public_turn_history"


CHARACTERISTICS = frozenset({"power", "toughness", "mana_value"})
HISTORY_FACTS = frozenset({
    "life_gained", "life_lost", "spells_cast", "cards_drawn",
    "permanents_sacrificed", "creatures_died", "creatures_entered",
})


@dataclass(frozen=True, slots=True)
class ScalarEffectAmountSpec:
    origin: ScalarAmountOrigin
    characteristic: str | None = None
    history_fact: str | None = None
    coefficient: int = 1
    binding_id: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise PublicQueryAmountError("Unsupported scalar effect amount version")
        if not isinstance(self.origin, ScalarAmountOrigin):
            raise PublicQueryAmountError("Scalar amount requires a typed origin")
        if type(self.coefficient) is not int or self.coefficient not in {-2, -1, 1, 2}:
            raise PublicQueryAmountError("Scalar result coefficient is outside the closed vocabulary")
        if self.binding_id is not None and (type(self.binding_id) is not str or not self.binding_id):
            raise PublicQueryAmountError("Scalar declaration identity must be nonempty")
        characteristic_origin = self.origin in {
            ScalarAmountOrigin.SOURCE, ScalarAmountOrigin.TARGET, ScalarAmountOrigin.EVENT_OBJECT,
        }
        if characteristic_origin != (type(self.characteristic) is str and self.characteristic in CHARACTERISTICS):
            raise PublicQueryAmountError("Scalar characteristic does not match its origin")
        if not characteristic_origin and self.characteristic is not None:
            raise PublicQueryAmountError("This scalar origin has no characteristic")
        if self.origin is ScalarAmountOrigin.HISTORY:
            if type(self.history_fact) is not str or self.history_fact not in HISTORY_FACTS:
                raise PublicQueryAmountError("Scalar history fact is unsupported")
        elif self.history_fact is not None:
            raise PublicQueryAmountError("Only history amounts carry a history fact")

    @property
    def producer_identity(self) -> dict[str, Any]:
        return {"origin": self.origin.value, "characteristic": self.characteristic,
                "history_fact": self.history_fact}

    def to_dict(self) -> dict[str, Any]:
        return {"kind": SCALAR_AMOUNT_KIND, "schema_version": self.schema_version,
                **self.producer_identity, "coefficient": self.coefficient,
                "binding_id": self.binding_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ScalarEffectAmountSpec":
        fields = {"kind", "schema_version", "origin", "characteristic", "history_fact", "coefficient", "binding_id"}
        if not isinstance(value, Mapping) or set(value) != fields or value.get("kind") != SCALAR_AMOUNT_KIND:
            raise PublicQueryAmountError("Scalar amount fields are incomplete or unknown")
        try:
            origin = ScalarAmountOrigin(value["origin"])
        except (TypeError, ValueError) as exc:
            raise PublicQueryAmountError("Scalar amount origin is unsupported") from exc
        return cls(origin=origin, characteristic=value["characteristic"], history_fact=value["history_fact"],
                   coefficient=value["coefficient"], binding_id=value["binding_id"], schema_version=value["schema_version"])


def scalar_amount_specs(value: Any) -> tuple[ScalarEffectAmountSpec, ...]:
    if isinstance(value, Mapping):
        if value.get("kind") == SCALAR_AMOUNT_KIND:
            return (ScalarEffectAmountSpec.from_dict(value),)
        return tuple(spec for child in value.values() for spec in scalar_amount_specs(child))
    if isinstance(value, (list, tuple)):
        return tuple(spec for child in value for spec in scalar_amount_specs(child))
    return ()


def scope_scalar_amount_bindings(value: Any, source_scope: str) -> Any:
    if isinstance(value, Mapping):
        result = {key: scope_scalar_amount_bindings(child, source_scope) for key, child in value.items()}
        if result.get("kind") == SCALAR_AMOUNT_KIND and result.get("binding_id"):
            result["binding_id"] = f"{source_scope}:{result['binding_id']}"
        return result
    if isinstance(value, (list, tuple)):
        return type(value)(scope_scalar_amount_bindings(child, source_scope) for child in value)
    return value
