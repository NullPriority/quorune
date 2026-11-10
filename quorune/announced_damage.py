from __future__ import annotations

"""Typed announcement and resolution data for divided damage."""

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .characteristic_fragments import CharacteristicQuantitySpec
from .public_characteristic_aggregate_model import (
    CONTROLLED_CREATURES_POWER_QUERY,
    PublicCharacteristicAggregateSpec,
)


DAMAGE_ALLOCATION_FIELD = "damage_allocation"
ANNOUNCED_DAMAGE_CONTEXT = "announced_damage_allocation"
ANNOUNCED_DAMAGE_EFFECT_FIELD = "announced_allocation"
ANNOUNCED_DAMAGE_CAPABILITY = "damage.batch.announced_allocation"
VARIABLE_DAMAGE_ALLOCATION_CAPABILITY = "damage.batch.announced_variable_allocation"


@dataclass(frozen=True, slots=True)
class DamageAllocationSpec:
    total: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            raise ValueError("Damage allocation version is unsupported")
        if type(self.total) is not int or self.total < (1 if self.schema_version == 1 else 0):
            raise ValueError("Damage allocation total is outside its versioned range")

    def to_dict(self) -> dict[str, int]:
        return {"schema_version": self.schema_version, "total": self.total}

    @classmethod
    def from_dict(cls, value: Any) -> DamageAllocationSpec:
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "total"}:
            raise ValueError("Bound damage allocation fields are malformed")
        return cls(value["total"], value["schema_version"])

    def _validate_shares(self, shares: Any, *, count: int) -> tuple[int, ...]:
        if (not isinstance(shares, (list, tuple)) or len(shares) != count
                or any(type(share) is not int or share < 1 for share in shares)
                or sum(shares) != (self.total if count else 0)):
            raise ValueError("Assign positive integer damage to every chosen target and use the full total")
        return tuple(shares)

    def announce(self, targets: Sequence[str], submitted: Any) -> dict[str, Any]:
        if not isinstance(submitted, Mapping) or set(submitted) != set(targets) or len(targets) != len(submitted):
            raise ValueError("Damage allocation must match the chosen targets exactly")
        shares = self._validate_shares([submitted[ref] for ref in targets], count=len(targets))
        return {
            "schema_version": self.schema_version, "shares": list(shares),
            **({"total": self.total} if self.schema_version == 2 else {}),
        }

    def read_announcement(self, value: Any, *, count: int) -> tuple[int, ...]:
        fields = {"schema_version", "shares"} | ({"total"} if self.schema_version == 2 else set())
        if (not isinstance(value, Mapping) or set(value) != fields
                or type(value["schema_version"]) is not int or value["schema_version"] != self.schema_version
                or self.schema_version == 2 and (type(value["total"]) is not int or value["total"] != self.total)):
            raise ValueError("The recorded damage allocation is malformed")
        return self._validate_shares(value["shares"], count=count)


@dataclass(frozen=True, slots=True)
class VariableDamageAllocationSpec:
    """One cast-announced total, derived before the cost plan can change the board."""

    quantity: CharacteristicQuantitySpec | PublicCharacteristicAggregateSpec | None = None
    increment: int = 0
    double_from: int | None = None

    def __post_init__(self) -> None:
        if (type(self.increment) is not int or self.increment not in (0, 1)
                or self.double_from is not None and (type(self.double_from) is not int or self.double_from != 6)
                or self.increment and self.double_from is not None
                or self.quantity is not None and (self.increment or self.double_from is not None)):
            raise ValueError("Unsupported variable damage arithmetic")
        if isinstance(self.quantity, CharacteristicQuantitySpec):
            from .characteristic_fragments import CharacteristicQuantityReduction, CharacteristicQuantityScope
            from .query_effect_amount_model import PublicQueryAmountSpec

            PublicQueryAmountSpec(self.quantity)
            if (self.quantity.schema_version != 1 or self.quantity.query is None
                    or self.quantity.query.zones != ("battlefield",)
                    or self.quantity.scope not in (CharacteristicQuantityScope.CONTROLLER_ZONE, CharacteristicQuantityScope.ALL_ZONES)
                    or self.quantity.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
                    or self.quantity.exclude_source or self.quantity.exclude_attached_object
                    or self.quantity.multiplier != 1 or self.quantity.offset != 0):
                raise ValueError("Announced damage counts require public battlefield objects")
        elif self.quantity is not None and not (
            isinstance(self.quantity, PublicCharacteristicAggregateSpec)
            and self.quantity.characteristic == "power" and self.quantity.reduction == "maximum"
            and self.quantity.controller_relation == "source_controller"
            and self.quantity.query == CONTROLLED_CREATURES_POWER_QUERY
        ):
            raise ValueError("Announced damage requires a closed public power aggregate")

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        from .characteristic_fragments import characteristic_quantity_capabilities

        if isinstance(self.quantity, PublicCharacteristicAggregateSpec):
            return (VARIABLE_DAMAGE_ALLOCATION_CAPABILITY, *self.quantity.capability_dependencies)
        if self.quantity is not None:
            return (VARIABLE_DAMAGE_ALLOCATION_CAPABILITY, *characteristic_quantity_capabilities(self.quantity.to_dict()))
        return (VARIABLE_DAMAGE_ALLOCATION_CAPABILITY,)

    def to_dict(self) -> dict[str, Any]:
        if self.quantity is None:
            amount = {"kind": "x", "increment": self.increment, "double_from": self.double_from}
        else:
            amount = {
                "kind": "count" if isinstance(self.quantity, CharacteristicQuantitySpec) else "aggregate",
                "quantity": self.quantity.to_dict(),
            }
        return {"schema_version": 2, "amount": amount}

    @classmethod
    def from_dict(cls, value: Any) -> VariableDamageAllocationSpec:
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "amount"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 2
                or not isinstance(value["amount"], Mapping)):
            raise ValueError("Variable damage allocation fields are malformed")
        amount = value["amount"]
        kind = amount.get("kind")
        if kind == "x" and set(amount) == {"kind", "increment", "double_from"}:
            return cls(increment=amount["increment"], double_from=amount["double_from"])
        if kind in ("count", "aggregate") and set(amount) == {"kind", "quantity"}:
            quantity_type = CharacteristicQuantitySpec if kind == "count" else PublicCharacteristicAggregateSpec
            return cls(quantity=quantity_type.from_dict(amount["quantity"]))
        raise ValueError("Variable damage amount has an unsupported source")

    def total_for_x(self, x: int) -> int:
        if self.quantity is not None or type(x) is not int or x < 0:
            raise ValueError("Damage allocation requires a nonnegative announced X")
        return 2 * x if self.double_from is not None and x >= self.double_from else x + self.increment

    def bind(self, host: Any, source: Any, actor: str, x: int | None) -> DamageAllocationSpec:
        if self.quantity is None:
            total = self.total_for_x(0 if x is None else x)
        elif x is not None:
            raise ValueError("The current battlefield defines this spell's damage total")
        elif isinstance(self.quantity, PublicCharacteristicAggregateSpec):
            from .public_characteristic_aggregates import current_public_characteristic_aggregate

            total = max(0, current_public_characteristic_aggregate(host, actor, self.quantity))
        else:
            from .dynamic_characteristics import query_characteristic_count

            total = query_characteristic_count(host, replace(source, controller=actor), self.quantity, _maximum_layer=None)
        return DamageAllocationSpec(total, schema_version=2)

def variable_damage_allocation(schema: Mapping[str, Any] | None) -> VariableDamageAllocationSpec | None:
    raw = (schema or {}).get(DAMAGE_ALLOCATION_FIELD)
    return VariableDamageAllocationSpec.from_dict(raw) if isinstance(raw, Mapping) and "amount" in raw else None


def damage_allocation_spec(schema: Mapping[str, Any] | None) -> DamageAllocationSpec | VariableDamageAllocationSpec | None:
    if not schema or DAMAGE_ALLOCATION_FIELD not in schema:
        return None
    from .targets import target_plan

    spec = variable_damage_allocation(schema) or DamageAllocationSpec.from_dict(schema[DAMAGE_ALLOCATION_FIELD])
    groups = target_plan(schema).groups
    if (len(groups) != 1 or not groups[0].distinct or groups[0].allow_reuse
            or isinstance(spec, DamageAllocationSpec) and groups[0].max_targets > spec.total or schema.get("modes")):
        raise ValueError("Damage allocation requires one bounded group of distinct targets")
    return spec


def _with_allocation_bound(schema, limit: int, spec: DamageAllocationSpec | None = None) -> dict[str, Any]:
    group, = schema["groups"]
    # The unbound variable group uses zero for an unrestricted printed target count.
    printed_limit = group["max"]
    maximum = min(printed_limit, limit) if printed_limit else limit
    return {**schema, "groups": [{**group, "max": maximum}],
            **({DAMAGE_ALLOCATION_FIELD: spec.to_dict()} if spec is not None else {})}


def bind_cast_allocation_option(host, actor, source, schema, option, response, *, hint, has_x):
    """Use the same allocation bounds when advertising and validating a cast."""
    try:
        spec = variable_damage_allocation(schema)
        if spec is None:
            return option
        if option.get("target_schema") is not None or spec.quantity is None and not has_x:
            return None
        capacity = len(host.state.cards) + len(host.state.players)
        if hint and spec.quantity is None and option.get("x_value_policy") != "zero":
            target_schema = _with_allocation_bound(schema, capacity)
        else:
            bound = spec.bind(host, source, actor, response.get("x"))
            target_schema = _with_allocation_bound(schema, min(capacity, bound.total), bound)
    except (TypeError, ValueError):
        return None
    return {**option, "target_schema": target_schema}


def preview_cast_allocation_schema(schema, option):
    spec = variable_damage_allocation(schema)
    if spec is None:
        return schema
    maximum = option.get("choice_schema", {}).get("x", {}).get("maximum")
    if spec.quantity is not None or type(maximum) is not int:
        raise ValueError("The variable allocation offer has no payable X")
    return _with_allocation_bound(schema, spec.total_for_x(maximum))


def announced_allocation_details(
    schema: Mapping[str, Any] | None,
    targets: Sequence[str],
    response: Mapping[str, Any],
) -> dict[str, Any]:
    spec = damage_allocation_spec(schema)
    if spec is None:
        return {}
    if isinstance(spec, VariableDamageAllocationSpec):
        raise ValueError("Variable damage requires a cast-bound allocation")
    submitted = response.get(DAMAGE_ALLOCATION_FIELD, None if targets else {})
    return {ANNOUNCED_DAMAGE_CONTEXT: spec.announce(targets, submitted)}


def cast_allocation_details(
    schema: Mapping[str, Any] | None,
    targets: Sequence[str],
    response: Mapping[str, Any],
) -> dict[str, Any]:
    from .rules.casting.model import CastProposalError

    try:
        if DAMAGE_ALLOCATION_FIELD in response and (schema is None or DAMAGE_ALLOCATION_FIELD not in schema):
            raise ValueError("This spell has no announced damage allocation")
        return announced_allocation_details(schema, targets, response)
    except ValueError as exc:
        raise CastProposalError(str(exc), reason="invalid_damage_allocation") from exc


def validate_announced_damage_effect(effect: Mapping[str, Any]) -> DamageAllocationSpec | VariableDamageAllocationSpec:
    if (set(effect) != {"op", "source", "amount", ANNOUNCED_DAMAGE_EFFECT_FIELD}
            or effect["op"] != "damage_fixed_set" or effect["source"] != "$source"
            or effect[ANNOUNCED_DAMAGE_EFFECT_FIELD] is not True):
        raise ValueError("Announced damage requires its exact source and allocation marker")
    amount = effect["amount"]
    return VariableDamageAllocationSpec.from_dict(amount) if isinstance(amount, Mapping) else DamageAllocationSpec(amount)


@dataclass(frozen=True, slots=True)
class AllocatedDamageRecipient:
    ref: str
    logical_object_id: str | None
    amount: int

    def __post_init__(self) -> None:
        if (type(self.ref) is not str or not self.ref or type(self.amount) is not int or self.amount < 1
                or self.logical_object_id is not None
                and (type(self.logical_object_id) is not str or not self.logical_object_id)):
            raise ValueError("Allocated damage recipient is malformed")

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "logical_object_id": self.logical_object_id, "amount": self.amount}

    @classmethod
    def from_dict(cls, value: Any) -> AllocatedDamageRecipient:
        if not isinstance(value, Mapping) or set(value) != {"ref", "logical_object_id", "amount"}:
            raise ValueError("Allocated damage recipient fields are missing or unknown")
        return cls(**dict(value))


def bind_announced_damage_recipients(host, effect, item) -> tuple[int, list[dict[str, Any]]]:
    from .rules.damage_snapshots import recipient_snapshot

    spec = validate_announced_damage_effect(effect)
    if item.context.get("targets_revalidated") is not True:
        raise ValueError("Announced damage requires revalidated target slots")
    announcement = item.context.get(ANNOUNCED_DAMAGE_CONTEXT)
    if isinstance(spec, VariableDamageAllocationSpec):
        bound = damage_allocation_spec(item.context.get("target_schema_override"))
        if (not isinstance(bound, DamageAllocationSpec) or bound.schema_version != 2
                or spec.quantity is None and bound.total != spec.total_for_x(0 if item.x_value is None else item.x_value)):
            raise ValueError("The cast did not seal its declared damage total")
        spec = bound
    shares = spec.read_announcement(announcement, count=len(item.targets))
    recipients = []
    for ref, amount in zip(item.targets, shares, strict=True):
        if ref is None:
            continue
        current = recipient_snapshot(host, ref, actor=item.controller)
        recipients.append(AllocatedDamageRecipient(ref, current.logical_object_id, amount).to_dict())
    return spec.total, recipients
