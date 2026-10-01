from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .characteristic_fragments import (
    CharacteristicFragmentError,
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
)
from .object_predicate import ObjectQueryError, ObjectQuerySpec


PUBLIC_QUERY_AMOUNT_KIND = "public_query_effect_amount"
PUBLIC_QUERY_AMOUNT_CAPABILITY = "quantity_expression.public_query_effect_amount"
CAST_X_AMOUNT_KIND = "cast_x_effect_amount"
DECLARED_AMOUNT_CAPABILITY = "quantity_expression.declared_effect_amount"


class PublicQueryAmountError(ValueError):
    """A resolution-time query-derived effect amount is malformed."""


_ALLOWED_SCOPES = frozenset(
    {
        CharacteristicQuantityScope.CONTROLLER_ZONE,
        CharacteristicQuantityScope.ALL_ZONES,
    }
)
_ALLOWED_ZONES = frozenset({"battlefield", "graveyard", "hand"})


def _query_is_layer_five_closed(query: ObjectQuerySpec) -> bool:
    """Keep effect quantities on the existing cycle-safe layer-5 boundary."""

    if (
        len(query.zones) != 1
        or query.zones[0] not in _ALLOWED_ZONES
        or query.owner is not None
        or query.controller is not None
        or query.excluded_controllers
        or query.keywords_all
        or query.keywords_none
        or query.tapped is not None
        or query.include_phased_out
        or query.known_to_actor is not None
        or query.exclude_ref is not None
        or query.state_predicate is not None
    ):
        return False
    if query.zones == ("hand",):
        return query == ObjectQuerySpec(zones=("hand",))
    return True


@dataclass(frozen=True, slots=True)
class PublicQueryAmountSpec:
    """One signed coefficient over a current public object-query count."""

    quantity: CharacteristicQuantitySpec
    coefficient: int = 1
    schema_version: int = 1
    binding_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise PublicQueryAmountError(
                "Unsupported public query effect amount schema version"
            )
        if self.schema_version == 1 and self.binding_id is not None:
            raise PublicQueryAmountError("Independent amounts cannot carry a declaration binding")
        if self.schema_version == 2 and (
            type(self.binding_id) is not str or not self.binding_id
        ):
            raise PublicQueryAmountError("Declared amounts require an explicit instruction identity")
        if type(self.coefficient) is not int or self.coefficient == 0:
            raise PublicQueryAmountError(
                "Public query effect amount coefficient must be a nonzero integer"
            )
        if self.schema_version == 2 and self.coefficient not in {-1, 1}:
            raise PublicQueryAmountError("Declared result coefficients are exactly one or minus one")
        if not isinstance(self.quantity, CharacteristicQuantitySpec):
            raise PublicQueryAmountError(
                "Public query effect amount requires a typed quantity"
            )
        if (
            self.quantity.scope not in _ALLOWED_SCOPES
            or self.quantity.query is None
            or self.quantity.exclude_source
            or self.quantity.exclude_attached_object
            or not _query_is_layer_five_closed(self.quantity.query)
        ):
            raise PublicQueryAmountError(
                "Public query effect amount requires a cycle-safe public zone query"
            )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "kind": PUBLIC_QUERY_AMOUNT_KIND,
            "schema_version": self.schema_version,
            "coefficient": self.coefficient,
            "quantity": self.quantity.to_dict(),
        }
        if self.schema_version == 2:
            result["binding_id"] = self.binding_id
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PublicQueryAmountSpec":
        expected = {
            "kind",
            "schema_version",
            "coefficient",
            "quantity",
        }
        if not isinstance(value, Mapping) or set(value) not in (expected, expected | {"binding_id"}):
            raise PublicQueryAmountError(
                "Public query effect amount fields are incomplete or unknown"
            )
        if (value.get("schema_version") == 2) != ("binding_id" in value):
            raise PublicQueryAmountError("Declaration fields must match the amount version")
        if value.get("kind") != PUBLIC_QUERY_AMOUNT_KIND:
            raise PublicQueryAmountError(
                "Public query effect amount kind is unsupported"
            )
        try:
            quantity = CharacteristicQuantitySpec.from_dict(value["quantity"])
        except (CharacteristicFragmentError, ObjectQueryError, TypeError, ValueError) as exc:
            raise PublicQueryAmountError(str(exc)) from exc
        return cls(
            quantity=quantity,
            coefficient=value["coefficient"],
            schema_version=value["schema_version"],
            binding_id=value.get("binding_id"),
        )


@dataclass(frozen=True, slots=True)
class CastXAmountSpec:
    """A signed reference to the canonical stack object's announced cost X."""

    coefficient: int = 1
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise PublicQueryAmountError("Unsupported cast-X amount version")
        if type(self.coefficient) is not int or self.coefficient not in {-1, 1}:
            raise PublicQueryAmountError("Cast-X result coefficients are exactly one or minus one")

    def to_dict(self) -> dict[str, Any]:
        return {"kind":CAST_X_AMOUNT_KIND,"schema_version":self.schema_version,"coefficient":self.coefficient}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CastXAmountSpec":
        if not isinstance(value, Mapping) or set(value) != {"kind", "schema_version", "coefficient"} or value.get("kind") != CAST_X_AMOUNT_KIND:
            raise PublicQueryAmountError("Cast-X amounts have a closed schema")
        return cls(coefficient=value["coefficient"], schema_version=value["schema_version"])


def scope_declared_amount_bindings(value: Any, source_scope: str) -> Any:
    """Give separate Oracle nodes separate declarations, even for identical prose."""
    if isinstance(value, Mapping):
        if value.get("kind") == PUBLIC_QUERY_AMOUNT_KIND and value.get("schema_version") == 2:
            spec = PublicQueryAmountSpec.from_dict(value)
            return replace(spec, binding_id=f"{source_scope}:{spec.binding_id}").to_dict()
        return {key:scope_declared_amount_bindings(child, source_scope) for key,child in value.items()}
    if isinstance(value, (list, tuple)):
        return [scope_declared_amount_bindings(child, source_scope) for child in value]
    return value


__all__ = [
    "CAST_X_AMOUNT_KIND",
    "DECLARED_AMOUNT_CAPABILITY",
    "CastXAmountSpec",
    "PUBLIC_QUERY_AMOUNT_CAPABILITY",
    "PUBLIC_QUERY_AMOUNT_KIND",
    "PublicQueryAmountError",
    "PublicQueryAmountSpec",
    "scope_declared_amount_bindings",
]
