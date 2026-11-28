from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .characteristic_fragments import (
    CharacteristicFragmentError,
    CharacteristicQuantityReduction,
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
)
from .object_predicate import ObjectQueryError, ObjectQuerySpec, PermanentStatePredicateSpec
from .live_battlefield_quantity_model import LiveBattlefieldQuantitySpec, LIVE_BATTLEFIELD_QUANTITY_KIND
from .shared_type_doors import (
    RoomDoorQuantity, ROOM_DOOR_AMOUNT_KIND, ROOM_DOOR_AMOUNT_CAPABILITY, ROOM_UNLOCKED_COUNT_CAPABILITY,
)


PUBLIC_QUERY_AMOUNT_KIND = "public_query_effect_amount"
PUBLIC_QUERY_AMOUNT_CAPABILITY = "quantity_expression.public_query_effect_amount"
SOURCE_RELATIVE_EFFECT_COUNT_CAPABILITY = "quantity_expression.source_relative_effect_count"
CAST_X_AMOUNT_KIND = "cast_x_effect_amount"
DECLARED_AMOUNT_CAPABILITY = "quantity_expression.declared_effect_amount"
SCALED_ANNOUNCED_TARGET_DAMAGE_CAPABILITY = "damage.batch.scaled_announced_x_each_target"
OPPONENT_HAND_MILL_KIND = "mill_opponent_current_hand_count"
OPPONENT_HAND_MILL_CAPABILITY = "zone.mill.opponent_hand_count"
OPPONENT_HAND_MILL_MECHANIC = "opponent-hand-count-mill"


class PublicQueryAmountError(ValueError):
    """A resolution-time query-derived effect amount is malformed."""


@dataclass(frozen=True, slots=True)
class OpponentHandMillCount:
    """A live public hand count bound separately to each opponent in a Mill batch."""

    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise PublicQueryAmountError("Unsupported opponent hand Mill count version")

    def to_dict(self) -> dict[str, int | str]:
        return {"kind": OPPONENT_HAND_MILL_KIND, "schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, scope: str) -> "OpponentHandMillCount":
        if (scope != "controller_opponents" or not isinstance(value, Mapping)
                or set(value) != {"kind", "schema_version"} or value["kind"] != OPPONENT_HAND_MILL_KIND):
            raise PublicQueryAmountError("Hand-count Mill requires its closed active opponent scope")
        return cls(schema_version=value["schema_version"])


_ALLOWED_SCOPES = frozenset(
    {
        CharacteristicQuantityScope.CONTROLLER_ZONE,
        CharacteristicQuantityScope.ALL_ZONES,
    }
)
_ALLOWED_ZONES = frozenset({"battlefield", "graveyard", "hand"})


def _query_is_layer_five_closed(query: ObjectQuerySpec) -> bool:
    """Keep effect quantities on the existing cycle-safe layer-5 boundary."""

    if query.state_predicate is not None:
        return query == ObjectQuerySpec(
            zones=("battlefield",), types_all=("creature",),
            state_predicate=PermanentStatePredicateSpec(attacking=True),
        )

    if (
        len(query.zones) != 1
        or query.zones[0] not in _ALLOWED_ZONES
        or query.owner is not None
        or query.controller is not None
        or query.excluded_controllers
        or query.keywords_all
        or query.colors_none
        or query.keywords_none
        or query.tapped is not None and query.zones!=('battlefield',)
        or query.include_phased_out
        or query.known_to_actor is not None
        or query.exclude_ref is not None
    ):
        return False
    if query.zones == ("hand",):
        return query == ObjectQuerySpec(zones=("hand",))
    return True


def _quantity_is_public_amount_closed(
    quantity: CharacteristicQuantitySpec | LiveBattlefieldQuantitySpec | RoomDoorQuantity,
) -> bool:
    if isinstance(quantity, RoomDoorQuantity):
        return True
    if isinstance(quantity,CharacteristicQuantitySpec) and quantity.schema_version==5:return True
    if isinstance(quantity, LiveBattlefieldQuantitySpec):
        return True
    if quantity.scope is CharacteristicQuantityScope.CONTROLLER_OPPONENT_COUNT:
        return True  # The versioned producer codec excludes object/hidden fields.
    if quantity.schema_version == 3:
        return all(_quantity_is_public_amount_closed(term) for term in quantity.terms)
    return (quantity.scope in _ALLOWED_SCOPES and quantity.query is not None
            and (not quantity.exclude_source or quantity.schema_version == 7 and quantity.query.zones == ("battlefield",))
            and not quantity.exclude_attached_object
            and _query_is_layer_five_closed(quantity.query))


ATTACK_SHARED_TYPE_AMOUNT_VERSION = 4
ATTACK_SHARED_TYPE_AMOUNT_CAPABILITY = "quantity_expression.attack_shared_creature_type_count"
SOURCE_SHARED_TYPE_AMOUNT_VERSION = 5
SOURCE_SHARED_TYPE_AMOUNT_CAPABILITY = "quantity_expression.controlled_source_shared_creature_type_count"
ENTRY_SHARED_TYPE_AMOUNT_VERSION = 6
ENTRY_SHARED_TYPE_AMOUNT_CAPABILITY = "quantity_expression.controlled_entrant_shared_creature_type_count"

_ATTACK_SHARED_TYPE_QUANTITY = LiveBattlefieldQuantitySpec(
    controller_relation="any", exclude_source=True,
    query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",),
                          state_predicate=PermanentStatePredicateSpec(attacking=True)),
)


_SOURCE_SHARED_TYPE_QUANTITY = LiveBattlefieldQuantitySpec(
    controller_relation="source_controller", exclude_source=True,
    query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
)

_ENTRY_SHARED_TYPE_QUANTITY = replace(_SOURCE_SHARED_TYPE_QUANTITY, exclude_source=False)


@dataclass(frozen=True, slots=True)
class PublicQueryAmountSpec:
    """One signed coefficient over a current public object-query count."""

    quantity: CharacteristicQuantitySpec | LiveBattlefieldQuantitySpec | RoomDoorQuantity
    coefficient: int = 1
    schema_version: int = 1
    binding_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {
            1, 2, 3, ATTACK_SHARED_TYPE_AMOUNT_VERSION, SOURCE_SHARED_TYPE_AMOUNT_VERSION,
            ENTRY_SHARED_TYPE_AMOUNT_VERSION,
        }:
            raise PublicQueryAmountError(
                "Unsupported public query effect amount schema version"
            )
        if self.schema_version != 2 and self.binding_id is not None:
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
        if not isinstance(self.quantity, (CharacteristicQuantitySpec, LiveBattlefieldQuantitySpec, RoomDoorQuantity)):
            raise PublicQueryAmountError(
                "Public query effect amount requires a typed quantity"
            )
        if self.schema_version == 3:
            if self.quantity != CharacteristicQuantitySpec(
                scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
                exclude_source=True,
            ):
                raise PublicQueryAmountError("Attack-relative amounts require one controlled creature count")
        elif self.schema_version == ATTACK_SHARED_TYPE_AMOUNT_VERSION:
            if self.quantity != _ATTACK_SHARED_TYPE_QUANTITY or self.coefficient != 1:
                raise PublicQueryAmountError("Shared-type attack amounts require one exact live attacker count")
        elif self.schema_version == SOURCE_SHARED_TYPE_AMOUNT_VERSION:
            if self.quantity != _SOURCE_SHARED_TYPE_QUANTITY or self.coefficient != 1:
                raise PublicQueryAmountError("Shared source type amounts require one controlled other-creature count")
        elif self.schema_version == ENTRY_SHARED_TYPE_AMOUNT_VERSION:
            if self.quantity != _ENTRY_SHARED_TYPE_QUANTITY or self.coefficient != 1:
                raise PublicQueryAmountError("Shared entrant type amounts require one controlled creature count")
        elif not _quantity_is_public_amount_closed(self.quantity):
            raise PublicQueryAmountError(
                "Public query effect amount requires a cycle-safe public zone query"
            )

    @property
    def uses_source_relative_count(self) -> bool:
        return self.schema_version in {3, ATTACK_SHARED_TYPE_AMOUNT_VERSION, SOURCE_SHARED_TYPE_AMOUNT_VERSION} or (
            isinstance(self.quantity, CharacteristicQuantitySpec)
            and self.quantity.schema_version == 7 and self.quantity.exclude_source
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
            raw = value['quantity']
            kind = raw.get('kind') if isinstance(raw, Mapping) else None
            if kind == LIVE_BATTLEFIELD_QUANTITY_KIND:
                quantity = LiveBattlefieldQuantitySpec.from_dict(raw)
            elif kind == ROOM_DOOR_AMOUNT_KIND:
                quantity = RoomDoorQuantity.from_dict(raw)
            else:
                quantity = CharacteristicQuantitySpec.from_dict(raw)
        except (CharacteristicFragmentError, ObjectQueryError, TypeError, ValueError) as exc:
            raise PublicQueryAmountError(str(exc)) from exc
        return cls(
            quantity=quantity,
            coefficient=value["coefficient"],
            schema_version=value["schema_version"],
            binding_id=value.get("binding_id"),
        )


CONTROLLER_MAXIMUM_PERMANENT_MANA_AMOUNT = PublicQueryAmountSpec(CharacteristicQuantitySpec(
    scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
    query=ObjectQuerySpec(zones=("battlefield",)),
    schema_version=2,
    reduction=CharacteristicQuantityReduction.MAXIMUM_MANA_VALUE,
))


def attack_shared_type_amount_spec() -> PublicQueryAmountSpec:
    return PublicQueryAmountSpec(_ATTACK_SHARED_TYPE_QUANTITY, schema_version=ATTACK_SHARED_TYPE_AMOUNT_VERSION)


def source_shared_type_amount_spec() -> PublicQueryAmountSpec:
    return PublicQueryAmountSpec(_SOURCE_SHARED_TYPE_QUANTITY, schema_version=SOURCE_SHARED_TYPE_AMOUNT_VERSION)


def entry_shared_type_amount_spec() -> PublicQueryAmountSpec:
    return PublicQueryAmountSpec(_ENTRY_SHARED_TYPE_QUANTITY, schema_version=ENTRY_SHARED_TYPE_AMOUNT_VERSION)


def public_query_amount_runtime_dependencies(value: Mapping[str, Any]) -> tuple[str, ...]:
    spec = PublicQueryAmountSpec.from_dict(value)
    if isinstance(spec.quantity, RoomDoorQuantity):
        declaration = (DECLARED_AMOUNT_CAPABILITY,) if spec.schema_version == 2 else ()
        return (PUBLIC_QUERY_AMOUNT_CAPABILITY, ROOM_DOOR_AMOUNT_CAPABILITY,
                ROOM_UNLOCKED_COUNT_CAPABILITY, *declaration)
    if spec.schema_version == 2:
        return (PUBLIC_QUERY_AMOUNT_CAPABILITY, DECLARED_AMOUNT_CAPABILITY)
    if spec.schema_version == ATTACK_SHARED_TYPE_AMOUNT_VERSION:
        from .shared_creature_type_model import SHARED_CREATURE_TYPE_QUERY_CAPABILITY

        return (PUBLIC_QUERY_AMOUNT_CAPABILITY, ATTACK_SHARED_TYPE_AMOUNT_CAPABILITY, SHARED_CREATURE_TYPE_QUERY_CAPABILITY)
    if spec.schema_version == SOURCE_SHARED_TYPE_AMOUNT_VERSION:
        from .shared_creature_type_model import SHARED_CREATURE_TYPE_QUERY_CAPABILITY

        return (PUBLIC_QUERY_AMOUNT_CAPABILITY, SOURCE_SHARED_TYPE_AMOUNT_CAPABILITY,
                SHARED_CREATURE_TYPE_QUERY_CAPABILITY, "target.ability_source.current_or_lki")
    if spec.schema_version == ENTRY_SHARED_TYPE_AMOUNT_VERSION:
        from .shared_creature_type_model import SHARED_CREATURE_TYPE_QUERY_CAPABILITY

        return (PUBLIC_QUERY_AMOUNT_CAPABILITY, ENTRY_SHARED_TYPE_AMOUNT_CAPABILITY, SHARED_CREATURE_TYPE_QUERY_CAPABILITY)
    return (PUBLIC_QUERY_AMOUNT_CAPABILITY,)


@dataclass(frozen=True, slots=True)
class CastXAmountSpec:
    """A signed reference to the canonical stack object's announced cost X."""

    coefficient: int = 1
    schema_version: int = 1
    bonus: int = 0

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3, 4}:
            raise PublicQueryAmountError("Unsupported cast-X amount version")
        allowed = {1: {-1, 1}, 2: {-3, -2, 2, 3}, 3: {1}, 4: {5}}
        if type(self.coefficient) is not int or self.coefficient not in allowed[self.schema_version]:
            raise PublicQueryAmountError("Cast-X result coefficient does not match its version")
        expected_bonus = 2 if self.schema_version == 3 else 0
        if type(self.bonus) is not int or self.bonus != expected_bonus:
            raise PublicQueryAmountError("Cast-X result bonus does not match its version")

    def to_dict(self) -> dict[str, Any]:
        result = {"kind": CAST_X_AMOUNT_KIND, "schema_version": self.schema_version, "coefficient": self.coefficient}
        if self.schema_version == 3:
            result["bonus"] = self.bonus
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CastXAmountSpec":
        fields = {"kind", "schema_version", "coefficient"}
        if isinstance(value, Mapping) and value.get("schema_version") == 3:
            fields.add("bonus")
        if not isinstance(value, Mapping) or set(value) != fields or value.get("kind") != CAST_X_AMOUNT_KIND:
            raise PublicQueryAmountError("Cast-X amounts have a closed schema")
        return cls(coefficient=value["coefficient"], schema_version=value["schema_version"], bonus=value.get("bonus", 0))


def cast_x_runtime_dependencies(value: Any) -> tuple[str,...]:
    """Require the typed amount owner wherever a persisted X read appears."""
    if isinstance(value,Mapping):
        if value.get('kind')==CAST_X_AMOUNT_KIND:
            spec = CastXAmountSpec.from_dict(value)
            return (DECLARED_AMOUNT_CAPABILITY, SCALED_ANNOUNCED_TARGET_DAMAGE_CAPABILITY
                    ) if spec.schema_version == 4 else (DECLARED_AMOUNT_CAPABILITY,)
        return tuple(sorted({cap for child in value.values() for cap in cast_x_runtime_dependencies(child)}))
    if isinstance(value,(list,tuple)):
        return tuple(sorted({cap for child in value for cap in cast_x_runtime_dependencies(child)}))
    return ()


def source_relative_count_capabilities(value: Mapping[str, Any]) -> tuple[str, ...]:
    quantity = value.get("quantity")
    named_source = (isinstance(quantity, Mapping) and quantity.get("schema_version") == 7
        and quantity.get("exclude_source") is True)
    if value.get("schema_version") != 3 and not named_source:
        return ()
    spec = PublicQueryAmountSpec.from_dict(value)
    return (SOURCE_RELATIVE_EFFECT_COUNT_CAPABILITY,) if spec.uses_source_relative_count else ()


def scope_declared_amount_bindings(value: Any, source_scope: str) -> Any:
    """Give separate Oracle nodes separate declarations, even for identical prose."""
    if isinstance(value, Mapping):
        if value.get("kind") == "scalar_effect_amount":
            from .scalar_effect_amount_model import scope_scalar_amount_bindings
            return scope_scalar_amount_bindings(value, source_scope)
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
    "cast_x_runtime_dependencies",
    "PUBLIC_QUERY_AMOUNT_CAPABILITY",
    "PUBLIC_QUERY_AMOUNT_KIND",
    "SOURCE_RELATIVE_EFFECT_COUNT_CAPABILITY",
    "PublicQueryAmountError",
    "PublicQueryAmountSpec",
    "scope_declared_amount_bindings",
]


def controlled_creature_power_search_amount():
    """One closed maximum-power profile for tapped basic-land arrival."""
    from .public_characteristic_aggregate_model import (
        CONTROLLED_CREATURES_POWER_QUERY, PublicCharacteristicAggregateSpec,
    )
    from .scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    return ScalarEffectAmountSpec(
        ScalarAmountOrigin.PUBLIC_AGGREGATE,
        schema_version=3,
        aggregate=PublicCharacteristicAggregateSpec(
            'power', 'maximum', 'source_controller', CONTROLLED_CREATURES_POWER_QUERY,
        ),
    )


def query_bound_search_amount_from_dict(value):
    from .scalar_effect_amount_model import SCALAR_AMOUNT_KIND, ScalarEffectAmountSpec

    if isinstance(value, Mapping) and value.get('kind') == SCALAR_AMOUNT_KIND:
        return ScalarEffectAmountSpec.from_dict(value)
    return PublicQueryAmountSpec.from_dict(value)


def validate_query_bound_search_amount(amount, *, tapped_battlefield):
    if tapped_battlefield and amount == controlled_creature_power_search_amount():
        return
    if (not isinstance(amount, PublicQueryAmountSpec) or amount.schema_version != 1
            or amount.coefficient != 1 or amount.binding_id is not None):
        raise ValueError('Query-bounded search requires one unbound positive public quantity')
    allowed = (
        CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE,
            ObjectQuerySpec(zones=('battlefield',), types_all=('land',))),
        CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE,
            ObjectQuerySpec(zones=('battlefield',), types_all=('creature',), tapped=True)),
        CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE,
            ObjectQuerySpec(zones=('battlefield',), types_all=('enchantment',), subtypes_all=('shrine',))),
        CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE,
            ObjectQuerySpec(zones=('battlefield',)), schema_version=2,
            reduction=CharacteristicQuantityReduction.DISTINCT_COLORS),
    )
    quantity = amount.quantity
    if quantity not in allowed and not (isinstance(quantity, LiveBattlefieldQuantitySpec) and quantity.schema_version == 3):
        raise ValueError('Query-bounded search maximum is outside the closed public profiles')


def freeze_query_bound_search_amount(amount, binding_id):
    if isinstance(amount, PublicQueryAmountSpec):
        return replace(amount, schema_version=2, binding_id=binding_id)
    return replace(amount, binding_id=binding_id)


def matches_frozen_query_bound_search_amount(amount, frozen):
    binding_id = getattr(frozen, 'binding_id', None)
    return (isinstance(binding_id, str) and binding_id.startswith('query-search:')
            and binding_id.removeprefix('query-search:').isdigit()
            and frozen == freeze_query_bound_search_amount(amount, binding_id))


def query_bound_search_amount_capabilities(amount):
    from .characteristic_fragments import characteristic_quantity_capabilities
    from .scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY, ScalarEffectAmountSpec

    if isinstance(amount, ScalarEffectAmountSpec):
        return (SCALAR_AMOUNT_CAPABILITY, *amount.referenced_quantity_capabilities)
    return (PUBLIC_QUERY_AMOUNT_CAPABILITY, *characteristic_quantity_capabilities(amount.to_dict()))
