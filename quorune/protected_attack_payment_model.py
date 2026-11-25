"""Typed fixed declaration cost protecting a resolution controller until their next turn."""

from collections.abc import Mapping
from dataclasses import dataclass
from .continuous_effect_model import ContinuousEffectDuration
from .declaration_fragments import DeclarationCostTemplate


OPERATION = "temporary_protected_attack_payment"
EFFECT_KIND = "resolution_protected_attack_payment"
MECHANIC = "next-turn-protected-attack-payment"
CAPABILITY = "combat.attack.payment.temporary_protected_defender"
_FIELDS = {"effect_kind", "schema_version", "effect_id", "source_id", "timestamp",
           "expires_on_next_turn_of", "duration", "cost"}


def closed_protected_cost(cost):
    return (
        isinstance(cost, DeclarationCostTemplate)
        and cost.declarations == ("attack",)
        and cost.scope == "source_controller"
        and cost.includes_planeswalkers
        and cost.source_condition is None
        and len(cost.mana) == 1
        and cost.mana[0][0] == "GENERIC"
        and cost.template_id == "source-controller-fixed-mana-attack-tax-player-planeswalker-v1"
    )


@dataclass(frozen=True, slots=True)
class ResolvedProtectedAttackPayment:
    effect_id: str
    source_id: str
    timestamp: int
    expires_on_next_turn_of: str
    cost: DeclarationCostTemplate
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_NEXT_TURN

    def __post_init__(self):
        if (any(type(x) is not str or not x for x in (self.effect_id, self.source_id, self.expires_on_next_turn_of))
                or type(self.timestamp) is not int or self.timestamp < 0
                or not closed_protected_cost(self.cost)
                or self.duration is not ContinuousEffectDuration.UNTIL_NEXT_TURN):
            raise ValueError("Protected attack payment requires a fixed cost and a captured expiry seat")

    def to_dict(self):
        return {"effect_kind": EFFECT_KIND, "schema_version": 1,
            "effect_id": self.effect_id, "source_id": self.source_id, "timestamp": self.timestamp,
            "expires_on_next_turn_of": self.expires_on_next_turn_of,
            "duration": self.duration.value, "cost": self.cost.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != _FIELDS or value["effect_kind"] != EFFECT_KIND
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Protected attack payment has a closed state codec")
        return cls(value["effect_id"], value["source_id"], value["timestamp"],
                   value["expires_on_next_turn_of"], DeclarationCostTemplate.from_dict(value["cost"]),
                   ContinuousEffectDuration(value["duration"]))


def decode_protected_attack_payment(instruction):
    if (not isinstance(instruction, Mapping) or set(instruction) != {"op", "schema_version", "cost"}
            or instruction["op"] != OPERATION or type(instruction["schema_version"]) is not int
            or instruction["schema_version"] != 1):
        raise ValueError("Protected attack payment requires one closed versioned instruction")
    cost = DeclarationCostTemplate.from_dict(instruction["cost"])
    if not closed_protected_cost(cost):
        raise ValueError("Only the fixed protected player and planeswalker payment is represented")
    return cost


@dataclass(frozen=True, slots=True)
class TemporaryProtectedAttackPaymentIntent:
    actor: str
    source_id: str
    cost: DeclarationCostTemplate

    def __post_init__(self):
        if (type(self.actor) is not str or not self.actor or type(self.source_id) is not str or not self.source_id
                or not closed_protected_cost(self.cost)):
            raise ValueError("Protected attack payment requires its trusted resolution source")
