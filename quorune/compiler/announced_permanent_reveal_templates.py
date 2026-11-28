"""Compile a public X-prefix and optional permanent entry with a graveyard remainder."""

from . import regex_working_set as re
from ..rules.announced_permanent_reveal import MECHANIC, announced_permanent_reveal_effect

_INSTRUCTION = re.compile(
    r"Reveal the top X cards of your library\. You may put any number of permanent cards with mana value "
    r"X or less from among them onto the battlefield\. Then put all cards revealed this way that weren['’]t "
    r"put onto the battlefield into your graveyard\.",
    re.IGNORECASE,
)


def announced_permanent_reveal_template(text):
    if _INSTRUCTION.fullmatch(" ".join(text.split())) is None:
        return None
    return "announced-revealed-permanents-v1", (announced_permanent_reveal_effect(),), None, (MECHANIC,)
