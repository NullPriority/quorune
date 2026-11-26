from __future__ import annotations

"""Lower a bounded targeted discard followed by an independent optional copy."""

from ..rules.targeted_discard_spell_copy import MECHANIC, copy_offer
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
