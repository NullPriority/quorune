from __future__ import annotations

"""Closed source defaults around an optional fixed payment."""

from dataclasses import dataclass
from typing import Any, Mapping

from .fixed_effect_payment import FIXED_EFFECT_PAYMENT_CAPABILITY, RETURN_PAYMENT_KIND, FixedEffectPaymentSpec
from .rules.hybrid_resolution_payment import HYBRID_RESOLUTION_PAYMENT_KIND


SOURCE_MAINTENANCE_CAPABILITY = "trigger.source.fixed_maintenance"
SOURCE_MAINTENANCE_MECHANIC = "fixed-source-maintenance"
SOURCE_TAP_LIFE_DECLINE_CAPABILITY = "effect.choice.source_tap_fixed_life_decline"
_MAINTENANCE_FIELDS = {"op", "schema_version", "player", "source", "payment"}


@dataclass(frozen=True, slots=True)
class SourceMaintenanceSpec:
    payment: FixedEffectPaymentSpec | None = None
    schema_version: int = 3
    decline: str = "sacrifice"

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 3:
            raise ValueError("Source maintenance requires version 3")
        if self.payment is not None and not isinstance(self.payment, FixedEffectPaymentSpec):
            raise ValueError("Source maintenance requires a fixed typed payment")
        if self.payment is not None and self.payment.kind == HYBRID_RESOLUTION_PAYMENT_KIND:
            raise ValueError("Source maintenance has no hybrid payment authority")
        if self.payment is not None and self.payment.kind == RETURN_PAYMENT_KIND:
            raise ValueError("Source maintenance has no permanent return authority")
        if type(self.decline) is not str or self.decline not in {"sacrifice", "tap"}:
            raise ValueError("Source maintenance has an unknown decline result")
        if self.decline == "tap" and (
            self.payment is None or self.payment.kind != "life" or self.payment.amount < 1
        ):
            raise ValueError("The default self-tap requires one positive fixed life price")

    def effect(self) -> dict[str, Any]:
        return {
            "op": "offer_optional_mana_payment", "schema_version": 3,
            "player": "$controller", "source": "$source.zone_object",
            "payment": self.payment.to_dict() if self.payment else None,
            **({"decline": "tap"} if self.decline == "tap" else {}),
        }

    @classmethod
    def from_effect(cls, effect: Mapping[str, Any]) -> "SourceMaintenanceSpec":
        if not isinstance(effect, Mapping) or set(effect) not in (_MAINTENANCE_FIELDS, _MAINTENANCE_FIELDS | {"decline"}):
            raise ValueError("Source maintenance effect fields are malformed")
        if "decline" in effect and effect["decline"] != "tap":
            raise ValueError("Only a default self-tap adds an explicit decline field")
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
            decline=effect.get("decline", "sacrifice"),
        )

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(sorted({
            SOURCE_MAINTENANCE_CAPABILITY, FIXED_EFFECT_PAYMENT_CAPABILITY,
            *(("permanent.tap.effect", SOURCE_TAP_LIFE_DECLINE_CAPABILITY)
              if self.decline == "tap" else ("zone.change.destination_replacement",)),
            *(self.payment.capabilities if self.payment else ()),
        }))


def source_maintenance_node_capabilities(
    effect: Mapping[str, Any],
    *,
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: set[str],
) -> tuple[str, ...]:
    """Bind only source maintenance for the controller and its source incarnation."""
    if target_schema is not None or SOURCE_MAINTENANCE_MECHANIC not in mechanic_ids:
        return ()
    try:
        spec = SourceMaintenanceSpec.from_effect(effect)
    except (KeyError, TypeError, ValueError):
        return ()
    if effect["player"] != "$controller" or effect["source"] != "$source.zone_object":
        return ()
    payment = spec.payment
    if payment is not None and (
        payment.kind in {"discard", "exile", "mana_discard"} and payment.predicate.owner != "$controller"
        or payment.kind in {"sacrifice", "tap"} and payment.predicate.controller != "$controller"
        or payment.kind in {"remove_source_counter", "source_sacrifice"} and payment.source_ref != "$source.zone_object"
    ):
        return ()
    return spec.capabilities
