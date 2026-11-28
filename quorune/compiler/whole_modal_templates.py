from __future__ import annotations

"""Select complete ordinary or cost-associated modal spell productions."""

from dataclasses import replace

from ..rules.modal_selection import (
    COMMANDER_CAST_MODE_LIMIT, COMMANDER_CAST_MODE_LIMIT_FIELD, WIZARD_CAST_MODE_LIMIT,
)
from .modal_templates import fixed_choose_one_modal_spell_template, fixed_nonrepeating_modal_template
from .spree_templates import fixed_spree_modal_template, fixed_tiered_modal_template
from .entwine_templates import fixed_entwine_modal_template


_COMMANDER_CAST_BOTH_HEADER = (
    "Choose one. If you control a commander as you cast this spell, you may choose both instead."
)


def _commander_cast_modal_spell_template(material_rows, *, compile_effect):
    if len(material_rows) != 3 or material_rows[0][1] != _COMMANDER_CAST_BOTH_HEADER:
        return None
    original, _, span = material_rows[0]
    normalized = ((original, "Choose one or both —", span), *material_rows[1:])
    template = fixed_nonrepeating_modal_template(normalized, compile_effect=compile_effect)
    if template is None:
        return None
    schema = {**template.target_schema, COMMANDER_CAST_MODE_LIMIT_FIELD: dict(COMMANDER_CAST_MODE_LIMIT)}
    return replace(template, _target_schema=schema)


def _wizard_cast_modal_spell_template(material_rows, *, compile_effect):
    header = "Choose one. If you control a Wizard as you cast this spell, you may choose two instead."
    if len(material_rows) != 4 or material_rows[0][1] != header:
        return None
    original, _, span = material_rows[0]
    normalized = ((original, "Choose two —", span), *material_rows[1:])
    template = fixed_nonrepeating_modal_template(normalized, compile_effect=compile_effect)
    if template is None:
        return None
    schema = {
        **template.target_schema, "mode_count": 1, "min_modes": 1,
        COMMANDER_CAST_MODE_LIMIT_FIELD: dict(WIZARD_CAST_MODE_LIMIT),
    }
    return replace(template, minimum_modes=1, _target_schema=schema)


def whole_modal_spell_template(material_rows, *, compile_effect):
    from .kicked_modal_templates import fixed_kicked_modal_template

    kicked = fixed_kicked_modal_template(material_rows, compile_effect=compile_effect)
    if kicked is not None:
        return kicked
    from .escalate_tap_templates import fixed_escalate_tap_template

    escalate = fixed_escalate_tap_template(material_rows, compile_effect=compile_effect)
    if escalate is not None:
        return escalate
    from .escalate_mana_templates import fixed_escalate_mana_template

    mana_escalate = fixed_escalate_mana_template(material_rows, compile_effect=compile_effect)
    if mana_escalate is not None:
        return mana_escalate

    from .pawprint_mode_templates import fixed_pawprint_mode_template, fixed_repeatable_mode_template

    if repeated := fixed_repeatable_mode_template(material_rows, compile_effect=compile_effect):
        return repeated
    pawprint = fixed_pawprint_mode_template(material_rows, compile_effect=compile_effect)
    if pawprint is not None:
        return pawprint
    entwine=fixed_entwine_modal_template(material_rows,compile_effect=compile_effect)
    if entwine is not None:return entwine
    priced = fixed_tiered_modal_template(material_rows, compile_effect=compile_effect) or fixed_spree_modal_template(
        material_rows, compile_effect=compile_effect,
    )
    if priced is not None:
        return priced
    commander_modal = _commander_cast_modal_spell_template(material_rows, compile_effect=compile_effect)
    if commander_modal is not None:
        return commander_modal
    wizard_modal = _wizard_cast_modal_spell_template(material_rows, compile_effect=compile_effect)
    if wizard_modal is not None:
        return wizard_modal
    ordinary = fixed_choose_one_modal_spell_template(material_rows, compile_effect=compile_effect)
    if ordinary is not None:
        return ordinary
    expanded = fixed_nonrepeating_modal_template(material_rows, compile_effect=compile_effect)
    return expanded if expanded is not None and not expanded.context_prefix else None
