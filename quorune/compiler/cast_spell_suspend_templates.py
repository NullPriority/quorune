"""Lower the closed copy-and-suspend body over the ordinary cast binding."""

from ..event_spell_copy_model import EVENT_SPELL_COPY_MECHANIC
from ..rules.cast_spell_suspend import MECHANIC, TEMPLATE, cast_copy_then_suspend_effects, second_controller_cast_condition
from .oracle_source_text import without_parenthetical_reminder


_BODY = (
    "copy it, then exile the spell you cast with four time counters on it. "
    "if it doesn't have suspend, it gains suspend."
)


def cast_spell_suspend_template(binding, text):
    material = " ".join(without_parenthetical_reminder(text).split()).casefold().replace("’", "'")
    if (binding.event.value != "spell.cast" or binding.event_condition != second_controller_cast_condition()
            or material != _BODY):
        return None
    return (TEMPLATE, cast_copy_then_suspend_effects(), None, (EVENT_SPELL_COPY_MECHANIC, MECHANIC)), False
