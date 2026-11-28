"""One public card follows a fixed Mill and optional mana/life payment."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..replacement.immutable import FrozenMap, thaw_value
from .committed_mill_cards import PUBLIC_MILLED_CARDS_CAPABILITY
from .committed_quantity_followups import CommittedQuantityFollowup
from .effect_shape_walkers import nested_effects
from .milled_permanent_battlefield import MilledPermanent as MilledCardSnapshot


MILL_PAID_HAND_KIND = "choose_one_card_to_hand_after_mana_life"
MILL_PAID_HAND_CAPABILITY = "zone.mill.committed_paid_return"
MILL_HAND_PAYMENT_FIELD = "committed_mill_mana_life_payment"
MILL_HAND_CHOICE_FIELD = "committed_mill_hand_choice"
MILL_PAID_HAND_DEPENDENCIES = (
    MILL_PAID_HAND_CAPABILITY, "zone.mill.fixed", PUBLIC_MILLED_CARDS_CAPABILITY,
    "effect.choice.optional_fixed_mana_payment", "casting.additional_cost.fixed_life_payment",
    "zone.change.destination_replacement",
    "trigger.effect.fixed_event", "trigger.placement.apnap",
)
_FIRST_MAIN = {"all": [
    {"field": "player", "op": "eq", "value": "$source.controller"},
    {"field": "step", "op": "eq", "value": "main"},
    {"field": "phase", "op": "eq", "value": "precombat_main"},
]}


@dataclass(frozen=True, slots=True)
class MillPaidHandReturn:
    count: int
    generic: int
    life: int

    def __post_init__(self):
        if any(type(value) is not int or not 1 <= value <= 99 for value in (self.count, self.generic, self.life)):
            raise ValueError("Paid Mill return requires bounded positive fixed amounts")

    def to_dict(self):
        return {
            "kind": MILL_PAID_HAND_KIND, "schema_version": 1,
            "count": self.count, "generic": self.generic, "life": self.life,
        }

    @classmethod
    def from_dict(cls, value):
        if (
            not isinstance(value, Mapping)
            or set(value) != {"kind", "schema_version", "count", "generic", "life"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != MILL_PAID_HAND_KIND
        ):
            raise ValueError("Paid Mill return needs its exact versioned specification")
        return cls(value["count"], value["generic"], value["life"])

    def effect(self):
        return {"op": "mill", "player": "$controller", "count": self.count, "followup": self.to_dict()}

    def from_committed_result(self, host, result):
        from ..milling import current_public_milled_cards

        cards = tuple(
            MilledCardSnapshot(card.ref, card.object_id, card.logical_object_id,
                            card.zone, card.zone_change_counter).to_dict()
            for card in current_public_milled_cards(host, result)
        )
        return CommittedQuantityFollowup((FrozenMap({
            "op": "offer_optional_mana_payment", "schema_version": 2, "player": result.player,
            "cost": self.mana_requirements(),
            MILL_HAND_PAYMENT_FIELD: {"spec": self.to_dict(), "cards": cards},
        }),))

    def mana_requirements(self):
        return {"GENERIC": self.generic, **dict.fromkeys("WUBRGC", 0)}


def uses_mill_paid_hand_return(effect):
    followup = effect.get("followup")
    return isinstance(followup, Mapping) and followup.get("kind") == MILL_PAID_HAND_KIND


def mill_paid_hand_node_capabilities(effects, target_schema):
    if len(effects) != 1 or target_schema is not None:
        return ()
    effect, = effects
    try:
        result = MillPaidHandReturn.from_dict(effect["followup"])
    except (TypeError, ValueError, KeyError):
        return ()
    if type(effect.get("count")) is not int or thaw_value(effect) != result.effect():
        return ()
    return MILL_PAID_HAND_DEPENDENCIES


def mill_paid_hand_program_dependencies(program):
    leaves = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                   if uses_mill_paid_hand_return(effect) or MILL_HAND_PAYMENT_FIELD in effect
                   or MILL_HAND_CHOICE_FIELD in effect)
    if not leaves and MILL_PAID_HAND_CAPABILITY not in program.capability_dependencies:
        return ()
    required = mill_paid_hand_node_capabilities(program.effects, program.target_schema)
    if (
        not required or leaves != tuple(program.effects) or program.handlers or program.cost_schema is not None
        or (program.event, program.active_zone) != ("step.begin", "battlefield")
        or thaw_value(program.event_condition) != _FIRST_MAIN
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
    ):
        raise ValueError("Paid Mill return requires its exact first-main-phase trigger")
    return required


def mill_hand_cards(value, *, maximum):
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ValueError("Committed Mill return requires a bounded public card set")
    cards = tuple(MilledCardSnapshot.from_dict(row) for row in value)
    if len({row.ref for row in cards}) != len(cards) or len({row.object_id for row in cards}) != len(cards):
        raise ValueError("Committed Mill return cannot repeat a card identity")
    return cards


def mill_hand_payment(effect, *, actor, continuation=False):
    fields = {"op", "schema_version", "player", "cost", MILL_HAND_PAYMENT_FIELD}
    if continuation:
        fields |= {"_choice_actor", "_stack_label", "_legal_objects"}
    if (not isinstance(effect, Mapping) or set(effect) != fields
            or effect["op"] != "offer_optional_mana_payment" or effect["player"] != actor
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 2):
        raise ValueError("Paid Mill return changed its payer or choice shape")
    value = effect[MILL_HAND_PAYMENT_FIELD]
    if not isinstance(value, Mapping) or set(value) != {"spec", "cards"}:
        raise ValueError("Paid Mill return lacks its sealed card result")
    spec = MillPaidHandReturn.from_dict(value["spec"])
    if not isinstance(effect["cost"], Mapping) or any(type(amount) is not int for amount in effect["cost"].values()) or dict(effect["cost"]) != spec.mana_requirements():
        raise ValueError("Paid Mill return changed its mana obligation")
    return spec, mill_hand_cards(value["cards"], maximum=spec.count)


def mill_hand_choice(effect, *, actor):
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "player", MILL_HAND_CHOICE_FIELD}
            or effect["op"] != "choose_option" or effect["player"] != actor):
        raise ValueError("Paid Mill return changed its selection owner or shape")
    return mill_hand_cards(effect[MILL_HAND_CHOICE_FIELD], maximum=99)
