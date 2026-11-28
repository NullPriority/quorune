"""The complete fixed-count private choice between a tapped land and a card in hand."""

from . import regex_working_set as re
from .fixed_library_selection_templates import FIXED_LIBRARY_SELECTION_MECHANIC
from ..rules.private_look_land_or_hand import PRIVATE_LOOK_LAND_OR_HAND_MECHANIC, private_look_land_or_hand_effect


def private_look_land_or_hand_template(text):
    match = re.fullmatch(
        r"Look at the top (?P<count>two|three|four|five|[2-5]) cards of your library\. "
        r"You may put a land card from among them onto the battlefield tapped\. "
        r"If you don['’]t, put a card from among them into your hand\. "
        r"Put the rest on the bottom of your library in a random order\.",
        " ".join(text.split()), re.I,
    )
    if match is None:
        return None
    value = match["count"].casefold()
    count = int(value) if value.isdecimal() else {"two": 2, "three": 3, "four": 4, "five": 5}[value]
    return ("private-look-land-or-hand-random-bottom-v1", (private_look_land_or_hand_effect(count),), None,
            (FIXED_LIBRARY_SELECTION_MECHANIC, PRIVATE_LOOK_LAND_OR_HAND_MECHANIC))
