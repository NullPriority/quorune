"""Compile a closed printed-cost graveyard Flashback grant."""

from . import regex_working_set as re
from ..replacement.immutable import thaw_value
from ..rules.target_graveyard_flashback import INSTRUCTION, TARGET_SCHEMA
from ..temporary_graveyard_flashback import MECHANIC


_PRINTED = re.compile(r"Target instant or sorcery card in your graveyard gains flashback until end of turn\."
                      r" The flashback cost is equal to its mana cost\."
                      r"(?: \(You may cast that card from your graveyard for its flashback cost\. Then exile it\.\))?",
                      re.IGNORECASE)


def target_graveyard_flashback_template(text):
    if _PRINTED.fullmatch(" ".join(text.split())) is None:
        return None
    return ("target-graveyard-printed-flashback-v1", (thaw_value(INSTRUCTION),), thaw_value(TARGET_SCHEMA),
            (MECHANIC, "cr-115-targets"))
