from __future__ import annotations

"""Validate the shared authoritative APNAP instruction boundary."""

from ..errors import GameRuleError
from .fractional_counts import validate_apnap_count


def validate_apnap_choice_effect(host, effect, continuation):
    validate_apnap_count(effect)
    if effect.get("predicate") is not None:
        private_discard = (
            effect.get("zone") == "hand"
            and effect.get("then") == "discard"
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
            *(("hidden",) if private_discard else ()),
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
