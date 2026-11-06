from __future__ import annotations

"""Closed fixed Blight instructions and the shared payer predicate."""

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec


BLIGHT_OPERATION = "blight"
BLIGHT_MECHANIC = "fixed-controller-blight"
BLIGHT_CAPABILITY = "keyword_action.blight.fixed"
BLIGHT_PAYMENT_VERSION = 9
BLIGHT_COUNTER = "-1/-1"
BLIGHT_FIELDS = frozenset({"op", "schema_version", "player", "amount"})


def blight_creature_predicate(controller: str) -> ObjectQuerySpec:
    return ObjectQuerySpec(
        zones=("battlefield",), controller=controller, types_all=("creature",),
        known_to_actor=True,
    )


def fixed_blight_amount(effect, *, actor="$controller") -> int:
    if (
        not isinstance(effect, Mapping)
        or set(effect) != BLIGHT_FIELDS
        or effect["op"] != BLIGHT_OPERATION
        or type(effect["schema_version"]) is not int
        or effect["schema_version"] != 1
        or effect["player"] != actor
        or type(effect["amount"]) is not int
        or effect["amount"] <= 0
    ):
        raise ValueError("Blight requires its fixed amount and bound player")
    return effect["amount"]


def blight_decline_branch_is_closed(payment_kind, paid, declined) -> bool:
    if (
        not isinstance(paid, (list, tuple)) or paid
        or not isinstance(declined, (list, tuple)) or not 1 <= len(declined) <= 4
        or any(not isinstance(effect, Mapping) for effect in declined)
    ):
        return False
    return payment_kind == "blight" or (
        payment_kind == "mana" and len(declined) == 1 and declined[0].get("op") == BLIGHT_OPERATION
    )
