from __future__ import annotations

"""Lower one exact resolution-time owned-commander mana value declaration."""

from ..rules.owned_commander_amount import OWNED_COMMANDER_AMOUNT_MECHANIC, owned_commander_amount_effect


_ORACLE_PATTERN = (
    "you draw x cards and you lose x life, where x is the mana value "
    "of a commander you own on the battlefield or in the command zone."
)


def owned_commander_amount_template(text):
    if " ".join(text.split()).casefold() != _ORACLE_PATTERN:
        return None
    return (
        "owned-commander-mana-value-draw-life-v1", (owned_commander_amount_effect(),), None,
        (OWNED_COMMANDER_AMOUNT_MECHANIC,),
    )
