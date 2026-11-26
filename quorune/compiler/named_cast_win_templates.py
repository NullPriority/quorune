"""Recognize the single exact spell grammar for a game-long named cast."""

from . import regex_working_set as re
from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC
from ..rules.named_cast_win import NAMED_CAST_WIN_TEMPLATE, named_cast_win_effect


_NAMED_CAST_WIN = re.compile(
    r"If this spell was cast from your hand and you've cast another spell named (?P<name>[^.]+) this game, "
    r"you win the game[.] Otherwise, put (?P=name) into its owner's library seventh from the top and "
    r"you gain 7 life[.]",
    re.IGNORECASE,
)


def named_cast_win_template(text, *, source_name):
    match = _NAMED_CAST_WIN.fullmatch(text.replace("’", "'"))
    if match is None or match["name"] != source_name:
        return None
    return (NAMED_CAST_WIN_TEMPLATE, (named_cast_win_effect(source_name),), None,
            (RESOLUTION_CONDITION_MECHANIC,))
