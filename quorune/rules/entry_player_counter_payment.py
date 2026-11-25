from __future__ import annotations

"""A land-entry player's optional counter payment and fixed damage consequence."""

from collections.abc import Mapping

from ..damage_source import DamageSourceSnapshot
from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..replacement.immutable import FrozenMap, thaw_value
from .blight_model import BLIGHT_PAYMENT_VERSION, blight_creature_predicate


ENTRY_PLAYER_COUNTER_PAYMENT_FIELD = "entry_player_counter_payment_version"
ENTRY_PLAYER_COUNTER_PAYMENT_MECHANIC = "entry-player-counter-payment-or-damage"
ENTRY_PLAYER_COUNTER_PAYMENT_CAPABILITY = "trigger.event.entry_player_counter_payment"
ENTRY_PLAYER_REFERENCE = "$context.putting_player"


def entry_player_counter_payment_condition(subtype: str) -> dict:
    if subtype not in {"forest", "swamp"}:
        raise ValueError("Entry player payment requires its printed land subtype")
    return {"all": [
        {"field": "subtypes", "op": "contains_any", "value": [subtype]},
        {"field": "putting_player", "op": "truthy", "value": True},
    ]}


def _payment_spec(player: str) -> FixedEffectPaymentSpec:
    return FixedEffectPaymentSpec(
        "blight", schema_version=BLIGHT_PAYMENT_VERSION, amount=1,
        predicate=blight_creature_predicate(player),
    )


def entry_player_counter_payment_effect() -> dict:
    return {
        "op": "offer_optional_mana_payment", "schema_version": 2,
        ENTRY_PLAYER_COUNTER_PAYMENT_FIELD: 1, "player": ENTRY_PLAYER_REFERENCE,
        "payment": _payment_spec(ENTRY_PLAYER_REFERENCE).to_dict(), "effects": [],
        "declined_effects": [{"op": "damage", "source": "$source",
                              "target": ENTRY_PLAYER_REFERENCE, "amount": 3}],
    }


def uses_entry_player_counter_payment(effect) -> bool:
    return isinstance(effect, Mapping) and ENTRY_PLAYER_COUNTER_PAYMENT_FIELD in effect


def is_entry_player_counter_payment(effect) -> bool:
    return (
        isinstance(effect, Mapping)
        and type(effect.get(ENTRY_PLAYER_COUNTER_PAYMENT_FIELD)) is int
        and type(effect.get("schema_version")) is int
        and FrozenMap(effect) == FrozenMap(entry_player_counter_payment_effect())
    )


def entry_player_counter_payment_dependencies(program, effect) -> tuple[str, ...]:
    condition = FrozenMap(program.event_condition or {})
    if (
        not is_entry_player_counter_payment(effect)
        or program.event != "land.enter" or program.active_zone != "battlefield"
        or program.target_schema is not None or len(program.effects) != 1
        or not any(condition == FrozenMap(entry_player_counter_payment_condition(subtype))
                   for subtype in ("forest", "swamp"))
    ):
        raise ValueError("Entry player counter payment lost its original land occurrence")
    return entry_player_counter_payment_capabilities()


def entry_player_counter_payment_capabilities() -> tuple[str, ...]:
    return (
        ENTRY_PLAYER_COUNTER_PAYMENT_CAPABILITY, *_payment_spec(ENTRY_PLAYER_REFERENCE).capabilities,
        "damage.amount.positive", "damage.result.player_life",
    )


def bind_entry_player_counter_choice(item, effect, *, resuming_actor=None) -> None:
    if not uses_entry_player_counter_payment(effect):
        return
    player = item.context.get("putting_player")
    if (
        item.context.get("event") != "land.enter" or type(player) is not str or not player
        or not item.context.get("card_object_identity") or effect.get("player") != player
        or resuming_actor is not None and resuming_actor != player
    ):
        raise ValueError("Entry counter payment belongs to the player who put the land onto the battlefield")
    _, declined = resolved_entry_player_payment(effect, player, continuation=resuming_actor is not None)
    snapshot = declined[0]["source_snapshot"]
    if (snapshot["object_id"] != item.source_object_id
            or snapshot["logical_object_id"] != item.context.get("source_logical_object_id")):
        raise ValueError("Entry counter payment damage lost its source incarnation")


def resolved_entry_player_payment(effect, actor, *, continuation=False):
    expected = set(entry_player_counter_payment_effect())
    if continuation:
        expected |= {"_choice_actor", "_legal_objects", "_stack_label"}
    spec = _payment_spec(actor)
    declined = effect.get("declined_effects")
    if (
        set(effect) != expected or type(actor) is not str or not actor
        or effect.get("player") != actor or effect.get("op") != "offer_optional_mana_payment"
        or type(effect.get("schema_version")) is not int or effect["schema_version"] != 2
        or type(effect.get(ENTRY_PLAYER_COUNTER_PAYMENT_FIELD)) is not int
        or effect[ENTRY_PLAYER_COUNTER_PAYMENT_FIELD] != 1
        or effect.get("effects") not in ([], ())
        or FrozenMap(effect.get("payment", {})) != FrozenMap(spec.to_dict())
        or not isinstance(declined, (list, tuple)) or len(declined) != 1
    ):
        raise ValueError("Entry player counter payment has an invalid resolved shape")
    damage, = declined
    if (
        not isinstance(damage, Mapping) or set(damage) != {"op", "source", "target", "amount", "source_snapshot"}
        or damage["op"] != "damage" or damage["target"] != actor
        or type(damage["amount"]) is not int or damage["amount"] != 3
    ):
        raise ValueError("Declined entry counter payment requires its fixed source damage")
    snapshot = DamageSourceSnapshot.from_dict(thaw_value(damage["source_snapshot"]))
    if snapshot.ref != damage["source"] or snapshot.zone != "battlefield":
        raise ValueError("Declined entry counter payment requires its battlefield source")
    return spec, (FrozenMap(damage),)
