"""Read a selected creature’s current stat immediately before it is sacrificed."""

from ..errors import GameRuleError
from ..object_query import exact_numeric_characteristic
from ..relative_power_target import RelativePowerTargetError, current_effective_creature_power
from ..replacement.immutable import FrozenMap
from ..scalar_effect_amount_model import PaidCreatureStatSnapshot
from .committed_quantity_followups import CommittedQuantityFollowup


def capture_sacrifice_life_amount(host, intent, card) -> int | PaidCreatureStatSnapshot:
    result = intent.result
    if (card.logical_object_id != result.incarnation or card.controller != result.sacrifice_controller
            or card.zone != "battlefield" or card.phased_out):
        raise GameRuleError("The selected creature no longer matches its sacrifice incarnation")
    try:
        if result.characteristic == "power" and not result.draw_then_lose_life:
            amount = current_effective_creature_power(host, card)
        else:
            effective = host._effective_card_data(card)
            types, _, _ = host._type_parts(str(effective.get("type_line") or ""))
            amount = exact_numeric_characteristic(card, effective, "toughness") if "creature" in types else None
            if result.draw_then_lose_life and amount is not None:
                power = exact_numeric_characteristic(card, effective, "power")
                return PaidCreatureStatSnapshot(result.incarnation, power, amount)
    except (RelativePowerTargetError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if amount is None:
        raise GameRuleError("The selected sacrifice no longer has its creature characteristic")
    return amount


def committed_sacrifice_life_amount(host, intent, object_id: str, amount) -> int | CommittedQuantityFollowup:
    current = host.state.cards.get(object_id)
    result = intent.result
    # Destination replacement can send the creature elsewhere and still complete the sacrifice.
    committed = current is None or current.logical_object_id != result.incarnation
    if result.draw_then_lose_life:
        if not isinstance(amount, PaidCreatureStatSnapshot) or amount.logical_object_id != result.incarnation:
            raise GameRuleError("A committed sacrifice requires both signed stats from its original incarnation")
        if not committed:
            return CommittedQuantityFollowup(())
        effects = []
        if amount.power > 0:
            effects.append(FrozenMap({"op": "draw", "player": result.player, "count": amount.power, "private": True}))
        if amount.toughness > 0:
            effects.append(FrozenMap({"op": "lose_life", "player": result.player, "amount": amount.toughness}))
        return CommittedQuantityFollowup(tuple(effects))
    return max(0, amount) if committed else 0
