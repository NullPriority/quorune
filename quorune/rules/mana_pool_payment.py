from __future__ import annotations

"""Exact allocation of fungible mana within one provenance class."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any

from ..util import normalize_mana_bundle


MANA_POOL_PAYMENT_FIELD = "mana_pool_payment"
_COLORS = "WUBRGC"


class ManaPoolPaymentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PoolManaUnit:
    color: str
    snow: bool = False
    restriction: str | None = None
    retention: str | None = None

    @property
    def key(self) -> str:
        content = json.dumps((self.color, self.snow, self.restriction, self.retention),
            separators=(",", ":"), ensure_ascii=True)
        return "pool-" + sha256(content.encode("ascii")).hexdigest()[:24]

    def label(self) -> str:
        traits = []
        if self.snow:
            traits.append("Snow")
        if self.restriction:
            from ..mana_spend_rules import CommanderEntryCounterMana, CommanderScryMana, decode_source_bound_cast_mana

            rider = decode_source_bound_cast_mana(self.restriction)
            if isinstance(rider, CommanderScryMana):
                traits.append("Scry 1 on matching creature cast")
            elif isinstance(rider, CommanderEntryCounterMana):
                traits.append("Additional counters on your commander")
            else:
                traits.append(self.restriction.replace("_", " "))
        if self.retention == "end_of_combat":
            traits.append("Retained until end of combat")
        elif self.retention == "end_of_turn":
            traits.append("Retained until end of turn")
        return self.color + " · " + (" · ".join(traits) if traits else "Ordinary")


def unit_for_lot(color: str, lot: Any) -> PoolManaUnit:
    return PoolManaUnit(color, lot.snow, lot.restriction, lot.retention)


@dataclass(frozen=True, slots=True)
class ManaPoolAllocation:
    units: tuple[tuple[PoolManaUnit, int], ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.units, tuple)
            or any(not isinstance(unit, PoolManaUnit) or type(amount) is not int or amount <= 0
                   for unit, amount in self.units)
            or len({unit for unit, _ in self.units}) != len(self.units)
        ):
            raise ManaPoolPaymentError("An exact pool payment requires distinct positive mana quantities")

    def bundles(self) -> tuple[dict[str, int], dict[str, int]]:
        total, snow = normalize_mana_bundle(None), normalize_mana_bundle(None)
        for unit, amount in self.units:
            total[unit.color] += amount
            if unit.snow:
                snow[unit.color] += amount
        return total, snow

    def require_available(
        self,
        player: Any,
        restriction_allows: Callable[[str, str | None], bool],
        spend_context: str | None,
    ) -> None:
        available = mana_pool_units(player)
        for unit, amount in self.units:
            if amount > available.get(unit, 0):
                raise ManaPoolPaymentError("Selected mana is no longer available in the pool")
            if unit.restriction is not None and not restriction_allows(unit.restriction, spend_context):
                raise ManaPoolPaymentError("Selected mana cannot be spent on this cost")


def mana_pool_units(player: Any) -> dict[PoolManaUnit, int]:
    from ..mana_provenance import mana_provenance_lots

    ordinary = normalize_mana_bundle(player.mana_pool)
    units: dict[PoolManaUnit, int] = {}
    for lot in mana_provenance_lots(player):
        for color, amount in lot.bundle:
            unit = unit_for_lot(color, lot)
            units[unit] = units.get(unit, 0) + amount
            ordinary[color] -= amount
    if any(amount < 0 for amount in ordinary.values()):
        raise ManaPoolPaymentError("Mana provenance exceeds the authoritative pool")
    for color in _COLORS:
        if ordinary[color]:
            units[PoolManaUnit(color)] = ordinary[color]
    return units


def decode_pool_payment(raw: Any, player: Any) -> ManaPoolAllocation:
    available = {unit.key: unit for unit in mana_pool_units(player)}
    if not isinstance(raw, Mapping) or len(raw) > len(available):
        raise ManaPoolPaymentError("Select quantities for current mana pool entries")
    if any(type(key) is not str or key not in available or type(amount) is not int or amount <= 0
           for key, amount in raw.items()):
        raise ManaPoolPaymentError("Pool payment requires positive quantities for current mana entries")
    return ManaPoolAllocation(tuple((available[key], raw[key]) for key in sorted(raw)))


def _pool_choice_rows(units: Mapping[PoolManaUnit, int]) -> list[dict[str, Any]]:
    return [
        {"id": unit.key, "label": unit.label(), "color": unit.color, "maximum": amount}
        for unit, amount in sorted(
            units.items(), key=lambda row: (_COLORS.index(row[0].color), row[0].key)
        )
    ]


def payable_mana_pool_rows(
    player: Any,
    restriction_allows: Callable[[str, str | None], bool],
    spend_context: str | None,
) -> list[dict[str, Any]]:
    """Describe the payer's current units eligible for one cost."""
    return _pool_choice_rows({
        unit: amount
        for unit, amount in mana_pool_units(player).items()
        if unit.restriction is None or restriction_allows(unit.restriction, spend_context)
    })


def mana_pool_choice_context(state: Any, actor: str | None, kind: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
    """Add actor-local form metadata outside the recorded decision state."""
    if actor not in state.players or kind != "priority":
        return context
    units = mana_pool_units(state.players[actor])
    if not any(unit.snow or unit.restriction is not None or unit.retention is not None for unit in units):
        return context
    return {**context, "mana_pool_rows": _pool_choice_rows(units)}
