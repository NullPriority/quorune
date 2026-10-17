from __future__ import annotations

"""Select complete ordinary or cost-associated modal spell productions."""

from .modal_templates import fixed_choose_one_modal_spell_template, fixed_nonrepeating_modal_template
from .spree_templates import fixed_spree_modal_template
from .entwine_templates import fixed_entwine_modal_template


def whole_modal_spell_template(material_rows, *, compile_effect):
    entwine=fixed_entwine_modal_template(material_rows,compile_effect=compile_effect)
    if entwine is not None:return entwine
    spree = fixed_spree_modal_template(material_rows, compile_effect=compile_effect)
    if spree is not None:
        return spree
    ordinary = fixed_choose_one_modal_spell_template(material_rows, compile_effect=compile_effect)
    if ordinary is not None:
        return ordinary
    expanded = fixed_nonrepeating_modal_template(material_rows, compile_effect=compile_effect)
    return expanded if expanded is not None and not expanded.context_prefix else None
