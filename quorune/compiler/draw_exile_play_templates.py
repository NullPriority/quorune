"""Recognize the complete fixed draw-to-exile policy in Oracle text."""

from ..semantic_runtime.draw_exile_play import DEPENDENCIES, draw_exile_play_descriptor


_CLOSED_RULE = (
    "if you would draw a card, exile the top two cards of your library instead. "
    "you may play those cards this turn."
)


def draw_exile_play_replacement_template(text):
    if " ".join(text.split()).casefold() != _CLOSED_RULE:
        return None
    return "draw-exile-top-turn-play-v1", draw_exile_play_descriptor(), DEPENDENCIES
