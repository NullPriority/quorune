from __future__ import annotations

from . import regex_working_set as re
from ..rules.apnap_library_reveals import library_reveal_round_effects
from ..rules.public_life_gain_scopes import PUBLIC_LIFE_GAIN_MECHANIC


_ROUND = re.compile(
    r"Each player looks at the top five cards of their library and may reveal a land card and/or an instant or sorcery "
    r"card from among them\. Each player puts the cards they revealed this way into their hand and the rest on the "
    r"bottom of their library in a random order\. Each player gains 3 life\.", re.I,
)


def apnap_library_reveal_round_template(text):
    if _ROUND.fullmatch(" ".join(text.split())) is None:
        return None
    return (
        "apnap-optional-land-and-spell-reveal-v1", library_reveal_round_effects(), None,
        ("cr-119-life", "cr-101-the-magic-golden-rules", PUBLIC_LIFE_GAIN_MECHANIC),
    )
