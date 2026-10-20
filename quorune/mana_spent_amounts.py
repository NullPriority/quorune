from __future__ import annotations

"""A spell's committed colored-mana provenance, never copied casting decisions."""

from .query_effect_amount_model import PublicQueryAmountError
from .scalar_effect_amount_model import (
    ScalarAmountOrigin, scalar_amount_specs, SCALAR_AMOUNT_CAPABILITY, SCALAR_AMOUNT_MECHANIC,
)

MANA_COLOR_AMOUNT_CAPABILITY = 'quantity_expression.spent_mana_color_count'
MANA_COLOR_AMOUNT_MECHANIC = 'spent-mana-color-count-amount'


def mana_color_amount_dependencies(effects, mechanics):
    amounts = scalar_amount_specs(effects)
    spent = tuple(spec for spec in amounts if spec.origin is ScalarAmountOrigin.CAST_MANA_COLOR_COUNT)
    if not spent:
        if MANA_COLOR_AMOUNT_MECHANIC in mechanics:
            raise ValueError('Spent mana-color marker has no represented amount')
        return ()
    if (not {SCALAR_AMOUNT_MECHANIC, MANA_COLOR_AMOUNT_MECHANIC} <= set(mechanics)
            or len(spent) != len(amounts)):
        raise ValueError('Spent mana-color results require their complete scalar owner')
    return (MANA_COLOR_AMOUNT_CAPABILITY, SCALAR_AMOUNT_CAPABILITY)


def mana_color_amount_runtime_dependencies(program):
    required = mana_color_amount_dependencies(program.effects, program.coverage)
    if required and getattr(program, 'event', None) != 'resolve':
        raise ValueError('This spent mana-color reader requires a resolving spell')
    return required


def resolve_spent_mana_color_amount(item):
    colors = getattr(item, 'mana_colors_spent', None)
    kind = getattr(item, 'kind', None)
    if (kind not in {'spell', 'spell_copy'} or not isinstance(colors, (list, tuple))
            or any(type(color) is not str or color not in {'W', 'U', 'B', 'R', 'G'} for color in colors)
            or len(colors) != len(set(colors)) or (kind == 'spell_copy' and colors)):
        raise PublicQueryAmountError('Spent mana colors require the canonical spell payment provenance')
    return len(colors)
