from __future__ import annotations

"""Closed default-sacrifice resolution around an optional fixed payment."""

from dataclasses import dataclass
from typing import Any, Mapping

from .fixed_effect_payment import FIXED_EFFECT_PAYMENT_CAPABILITY, FixedEffectPaymentSpec


SOURCE_MAINTENANCE_CAPABILITY = "trigger.source.fixed_maintenance"
SOURCE_MAINTENANCE_MECHANIC = "fixed-source-maintenance"


@dataclass(frozen=True, slots=True)
class SourceMaintenanceSpec:
    payment: FixedEffectPaymentSpec | None = None
    schema_version: int = 3

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 3:
            raise ValueError("Source maintenance requires version 3")
        if self.payment is not None and not isinstance(self.payment, FixedEffectPaymentSpec):
            raise ValueError("Source maintenance requires a fixed typed payment")

    def effect(self) -> dict[str, Any]:
        return {
            "op": "offer_optional_mana_payment", "schema_version": 3,
            "player": "$controller", "source": "$source.zone_object",
            "payment": self.payment.to_dict() if self.payment else None,
        }

    @classmethod
    def from_effect(cls, effect: Mapping[str, Any]) -> "SourceMaintenanceSpec":
        if not isinstance(effect, Mapping) or set(effect) != {"op", "schema_version", "player", "source", "payment"}:
            raise ValueError("Source maintenance effect fields are malformed")
        if effect.get("op") != "offer_optional_mana_payment":
            raise ValueError("Source maintenance requires the registered payment operation")
        if type(effect['player']) is not str or not effect['player'] or (
            effect['source'] is not None and (type(effect['source']) is not str or not effect['source'])
        ):
            raise ValueError("Source maintenance requires a player and source reference")
        payment = effect["payment"]
        return cls(
            payment=FixedEffectPaymentSpec.from_dict(payment) if payment is not None else None,
            schema_version=effect["schema_version"],
        )

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(sorted({
            SOURCE_MAINTENANCE_CAPABILITY, FIXED_EFFECT_PAYMENT_CAPABILITY,
            "zone.change.destination_replacement",
            *(self.payment.capabilities if self.payment else ()),
        }))
