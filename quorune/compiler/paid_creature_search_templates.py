"""Parse the closed paid-creature upper-bound search and terminal exile."""

from ..rules.casting_additional_costs import fixed_creature_sacrifice_price
from ..rules.paid_creature_library_search import MECHANIC, TEMPLATE, paid_creature_search_effect
from ..rules.spell_self_exile import SELF_EXILE
from .spell_self_exile import terminal_self_exile_body

_SEARCH = (
    "search your library for a creature card with mana value x or less, where x is 2 plus the "
    "sacrificed creature's mana value. put that card onto the battlefield, then shuffle."
)


def paid_creature_search_template(text, *, cost_schema, source_name):
    if not fixed_creature_sacrifice_price(cost_schema):
        return None
    body = terminal_self_exile_body(text, source_name=source_name)
    if body is None or body.casefold().replace("’", "'") != _SEARCH:
        return None
    return (
        f"{SELF_EXILE.template}+{TEMPLATE}", (paid_creature_search_effect(),), None,
        (MECHANIC, SELF_EXILE.mechanic),
    )
