from __future__ import annotations

"""Lower a bounded targeted discard followed by an independent optional copy."""

from ..rules.targeted_discard_spell_copy import MECHANIC, copy_offer
from ..rules.targeted_return_spell_copy import RETURN_COPY_MECHANIC, return_copy_payment
from .return_to_hand_templates import targeted_return_to_hand_effect_template
from .affected_player_discard_templates import AffectedPlayerDiscardSubject, FixedAffectedPlayerDiscardTemplate


_ORACLE_PATTERN = (
    "target player discards two cards. "
    "that player may copy this spell and may choose a new target for that copy."
)


def targeted_discard_spell_copy_template(text):
    if " ".join(text.split()).casefold() != _ORACLE_PATTERN:
        return None
    discard = FixedAffectedPlayerDiscardTemplate(AffectedPlayerDiscardSubject.TARGET_PLAYER, 2)
    return (
        "targeted-fixed-discard-resolving-copy-v1", (*discard.effects, copy_offer()), discard.target_schema,
        (*discard.mechanics, MECHANIC),
    )


def targeted_return_spell_copy_template(text):
    normalized = " ".join(text.split()).casefold().replace("’", "'")
    first, separator, result = normalized.partition(". ")
    if not separator or result != (
        "then that permanent's controller may sacrifice a land of their choice. "
        "if the player does, they may copy this spell and may choose a new target for that copy."
    ):
        return None
    if first != "return target nonland permanent to its owner's hand":
        return None
    bounce = targeted_return_to_hand_effect_template(first)
    return ("targeted-nonland-return-land-sacrifice-copy-v1",
            (*bounce.effects, return_copy_payment()), bounce.target_schema,
            (*bounce.mechanics, RETURN_COPY_MECHANIC))
