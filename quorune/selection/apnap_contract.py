from __future__ import annotations

"""Validate the shared authoritative APNAP instruction boundary."""

from ..errors import GameRuleError
from ..library_order_model import HAND_TOP_APNAP_RESULT, is_affected_hand_top_choice
from ..apnap_object_payment import DECLINED_LIFE_LOSS, apnap_object_payment_dependencies
from ..object_predicate import ObjectQuerySpec
from ..player_maximum import current_players_at_maximum
from ..player_maximum_model import LAND_COUNT_METRIC, uses_land_count_maximizers
from .fractional_counts import validate_apnap_count


def bind_most_lands_sacrifice_players(host, effect, continuation):
    if not uses_land_count_maximizers(effect.get("players")):
        return effect

    from ..rules.affected_player_sacrifice_capability_shapes import most_lands_attack_sacrifice_effect_is_closed

    stack_ref = continuation.get("stack_ref")
    item = next((candidate for candidate in host.state.stack if candidate.ref == stack_ref), None)
    if (
        item is None or item.context.get("event") != "creature.attacks" or item.targets
        or not most_lands_attack_sacrifice_effect_is_closed(effect, actor=item.controller)
    ):
        raise GameRuleError("Most-lands sacrifice requires its exact sealed combat choice")
    try:
        players = current_players_at_maximum(
            host.state, LAND_COUNT_METRIC, public_object_resolver=host._public_object_query_result,
        )
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    return {**effect, "players": list(players)}


def is_typed_hand_exile(effect):
    return (
        effect.get("zone") == "hand" and effect.get("then") == "exile"
        and effect.get("predicate") is not None
    )


def validate_apnap_choice_effect(host, effect, continuation):
    validate_apnap_count(effect)
    hand_top = is_affected_hand_top_choice(effect)
    if hand_top and effect.get("predicate") is None:
        raise GameRuleError("Hand ordering requires a private own-hand predicate")
    try:
        optional_payment = bool(apnap_object_payment_dependencies(effect))
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    if effect.get("predicate") is not None:
        private_hand_choice = (
            effect.get("zone") == "hand"
            and effect.get("then") in {"discard", "exile", HAND_TOP_APNAP_RESULT}
        )
        allowed_fields = {
            "op",
            "actor",
            "players",
            "zone",
            "predicate",
            "count",
            "then",
            "prompt",
            *(("exclude_ref",) if "exclude_ref" in effect else ()),
            *(("require_full_count", "fallback_effects") if effect.get("require_full_count") is True else ()),
            *(("hidden",) if private_hand_choice else ()),
            *((DECLINED_LIFE_LOSS,) if optional_payment else ()),
            *(('target',) if "target" in effect else ()),
        }
        stack_ref = str(continuation.get("stack_ref") or "")
        item = next(
            (
                candidate
                for candidate in host.state.stack
                if candidate.ref == stack_ref
            ),
            None,
        )
        from ..rules.event_player_choice_shapes import attacked_defender_exile_effect_is_closed, is_typed_defender_exile

        if is_typed_defender_exile(effect):
            defender = item.context.get("defending_player") if item is not None else None
            if (
                item is None or item.context.get("event") != "creature.attacks"
                or type(defender) is not str or defender not in host.active_seats
                or not attacked_defender_exile_effect_is_closed(effect, actor=item.controller, player=defender)
            ):
                raise GameRuleError("Defender exile requires its exact sealed combat choice")
            return
        if (
            set(effect) != allowed_fields
            or effect.get("op") != "choose_cards_apnap"
            or (
                effect.get("zone"),
                effect.get("then"),
                effect.get("hidden", False),
            )
            not in {
                ("battlefield", "sacrifice", False),
                ("battlefield", "return_owner_hand", False),
                ("hand", "discard", True),
                ("hand", "exile", True),
                ("hand", HAND_TOP_APNAP_RESULT, True),
            }
            or type(effect.get("actor")) is not str
            or effect.get("actor") not in host.active_seats
            or item is None
            or effect.get("actor") != item.controller
            or (
                "target" in effect
                and effect.get("players") != [effect.get("target")]
            )
            or type(effect.get("prompt")) is not str
            or not str(effect.get("prompt")).strip()
            or (
                "exclude_ref" in effect
                and (type(effect.get("exclude_ref")) is not str or not effect.get("exclude_ref"))
            )
            or (
                effect.get("require_full_count") is True
                and (
                    effect.get("then") != "return_owner_hand"
                    or not isinstance(effect.get("fallback_effects"), list)
                    or not effect.get("fallback_effects")
                )
            )
        ):
            raise GameRuleError("Typed APNAP choice effect is malformed")
        if (is_typed_hand_exile(effect) or hand_top) and (
            type(effect.get("count")) is not int or effect["count"] not in {1, 2, 3}
            or effect["predicate"] != ObjectQuerySpec(zones=("hand",)).to_dict()
            or "exclude_ref" in effect or "require_full_count" in effect
            or ("target" not in effect and effect.get("players") not in ("all", "opponents"))
        ):
            if hand_top:
                raise GameRuleError("Hand ordering requires a fixed private own-hand choice")
            raise GameRuleError("Typed hand exile requires a fixed private own-hand choice")
        if hand_top and "target" in effect and effect["target"] not in item.targets:
            raise GameRuleError("The ordered hand choice lost its announced player target")


def revalidate_ordered_hand_choices(host, state, player_order):
    selected = state["selected"]
    incarnations = state.get("hand_top_incarnations", {})
    effect = state["effect"]
    expected = host.apnap_order()
    if "target" in effect:
        expected = [effect["target"]]
    elif effect["players"] == "opponents":
        expected = [seat for seat in expected if seat != effect["actor"]]
    if player_order != expected or set(selected) != set(expected):
        raise GameRuleError("Ordered hand choices are incomplete")
    for seat in player_order:
        cards = {
            card.ref: card.logical_object_id
            for object_id in host.state.players[seat].zones["hand"]
            if (card := host.state.cards[object_id]).owner == seat
        }
        if any(ref not in incarnations or cards.get(ref) != incarnations[ref] for ref in selected[seat]):
            raise GameRuleError("A selected hand card changed before simultaneous movement")
