"""Reuse the exact target-set and life leaves inside one announced-X mode choice."""

from ..rules.announced_x_modal import (
    ANNOUNCED_X_MODAL_MECHANIC, DESTROY_MODE, LIFE_MODE, validate_announced_x_modal_schema,
)
from ..util import parse_mana_symbols
from .announced_target_sets import announced_target_set_effect_template
from .declared_effect_amounts import declared_effect_amount_template
from .life_templates import fixed_life_effect_template
from .modal_templates import FIXED_CHOOSE_ONE_MODAL_MECHANIC, FixedChooseOneModalSpellTemplate

_HEADER = "Choose one —"
_DESTRUCTION = "destroy x target artifacts and/or enchantments."
_LIFE_GAIN = "target player gains twice x life."


def _compile_fixed_life(text):
    leaf = fixed_life_effect_template(text)
    return leaf.compiled() if leaf is not None else (None, (), None, ())


def announced_x_modal_template(record, material_rows):
    if (record.layout != "normal" or record.faces or parse_mana_symbols(record.mana_cost).count("X") != 1
            or len(material_rows) != 3 or material_rows[0][1] != _HEADER):
        return None
    first, second = (row[1] for row in material_rows[1:])
    if first.casefold() != "• " + _DESTRUCTION or second.casefold() != "• " + _LIFE_GAIN:
        return None
    destroy = announced_target_set_effect_template(first[2:])
    life = declared_effect_amount_template(second[2:], source_name=record.name,
                                          compile_fixed=_compile_fixed_life, cast_x_available=True)
    if destroy is None or life is None:
        return None
    destroy_mechanics = (*destroy[3], ANNOUNCED_X_MODAL_MECHANIC)
    schema = {"mode_count": 1, "modes": {
        DESTROY_MODE: {**destroy[2], "effects": list(destroy[1]), "mechanics": list(destroy_mechanics)},
        LIFE_MODE: {**life[2], "effects": list(life[1]), "mechanics": list(life[3])},
    }}
    try:
        validate_announced_x_modal_schema(schema)
    except (TypeError, ValueError, KeyError):
        return None
    return FixedChooseOneModalSpellTemplate(
        component_template_ids=(destroy[0], life[0]), _target_schema=schema,
        mechanic_ids=tuple(dict.fromkeys((FIXED_CHOOSE_ONE_MODAL_MECHANIC, *destroy_mechanics, *life[3]))),
    )
