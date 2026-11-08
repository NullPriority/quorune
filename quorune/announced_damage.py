from __future__ import annotations

"""Typed announcement and resolution data for fixed-total divided damage."""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


DAMAGE_ALLOCATION_FIELD = "damage_allocation"
ANNOUNCED_DAMAGE_CONTEXT = "announced_damage_allocation"
ANNOUNCED_DAMAGE_EFFECT_FIELD = "announced_allocation"
ANNOUNCED_DAMAGE_CAPABILITY = "damage.batch.announced_allocation"


@dataclass(frozen=True, slots=True)
class DamageAllocationSpec:
    total: int

    def __post_init__(self) -> None:
        if type(self.total) is not int or self.total < 1:
            raise ValueError("Damage allocation requires a positive integer total")

    def to_dict(self) -> dict[str, int]:
        return {"schema_version": 1, "total": self.total}

    @classmethod
    def from_dict(cls, value: Any) -> DamageAllocationSpec:
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "total"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Damage allocation has a closed version-one schema")
        return cls(value["total"])

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
        return {"schema_version": 1, "shares": list(shares)}

    def read_announcement(self, value: Any, *, count: int) -> tuple[int, ...]:
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "shares"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("The recorded damage allocation is malformed")
        return self._validate_shares(value["shares"], count=count)


def damage_allocation_spec(schema: Mapping[str, Any] | None) -> DamageAllocationSpec | None:
    if not schema or DAMAGE_ALLOCATION_FIELD not in schema:
        return None
    from .targets import target_plan

    spec = DamageAllocationSpec.from_dict(schema[DAMAGE_ALLOCATION_FIELD])
    groups = target_plan(schema).groups
    if (len(groups) != 1 or not groups[0].distinct or groups[0].allow_reuse
            or groups[0].max_targets > spec.total or schema.get("modes")):
        raise ValueError("Damage allocation requires one bounded group of distinct targets")
    return spec


def announced_allocation_details(
    schema: Mapping[str, Any] | None,
    targets: Sequence[str],
    response: Mapping[str, Any],
) -> dict[str, Any]:
    spec = damage_allocation_spec(schema)
    if spec is None:
        return {}
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


def validate_announced_damage_effect(effect: Mapping[str, Any]) -> DamageAllocationSpec:
    if (set(effect) != {"op", "source", "amount", ANNOUNCED_DAMAGE_EFFECT_FIELD}
            or effect["op"] != "damage_fixed_set" or effect["source"] != "$source"
            or effect[ANNOUNCED_DAMAGE_EFFECT_FIELD] is not True):
        raise ValueError("Announced damage requires its exact source and allocation marker")
    return DamageAllocationSpec(effect["amount"])


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


def bind_announced_damage_recipients(host, effect, item) -> list[dict[str, Any]]:
    from .rules.damage_snapshots import recipient_snapshot

    spec = validate_announced_damage_effect(effect)
    if item.context.get("targets_revalidated") is not True:
        raise ValueError("Announced damage requires revalidated target slots")
    shares = spec.read_announcement(item.context.get(ANNOUNCED_DAMAGE_CONTEXT), count=len(item.targets))
    recipients = []
    for ref, amount in zip(item.targets, shares, strict=True):
        if ref is None:
            continue
        current = recipient_snapshot(host, ref, actor=item.controller)
        recipients.append(AllocatedDamageRecipient(ref, current.logical_object_id, amount).to_dict())
    return recipients
