"""Closed spell production for repeated top-library exile and name collision."""

from ..rules.exile_until_same_name import EXILE_UNTIL_SAME_NAME_MECHANIC, exile_until_same_name_instruction


_TOP_EXILE = (
    "exile the top card of your library. "
    "you may put that card into your hand unless it has the same name as another card exiled this way. "
    "repeat this process until you put a card into your hand or "
    "you exile two cards with the same name, whichever comes first"
)


def exile_until_same_name_template(text: str):
    if " ".join(text.casefold().split()).removesuffix(".") != _TOP_EXILE:
        return None
    return ("exile-library-top-until-same-name-v1", (exile_until_same_name_instruction(),),
            None, (EXILE_UNTIL_SAME_NAME_MECHANIC,))
