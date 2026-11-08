from __future__ import annotations

"""Source-pinned preparation entry and alternative spell characteristics."""

import re

from ..preparation_model import (
    PrepareSpellSpec, PREPARATION_HANDLER, PREPARATION_CAPABILITY,
    PREPARATION_RESULT_MECHANIC, PREPARATION_RESULT_OPERATION,
)
from ..rules.source_references import SourceReferenceSpec
from ..rules.face_characteristic_values import printed_face_mana_value,printed_face_colors
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .static_runtime_nodes import runtime_handler_node


def preparation_spell(record, face_name):
    """Pin the copiable alternative of the preparation card's normal face."""
    if (record.layout != "prepare" or len(record.faces) != 2
            or face_name != record.faces[0].get("name")):
        return None
    inset = record.faces[1]
    try:
        return PrepareSpellSpec(
            record.oracle_id, str(inset["name"]), str(inset.get("mana_cost") or ""),
            printed_face_mana_value(inset), str(inset.get("type_line") or ""),
            tuple(printed_face_colors(inset)),
        )
    except (ValueError, TypeError, KeyError):
        return None


_SOURCE_PREPARATION = re.compile(r"(?P<source>.+?) becomes prepared\.?", re.IGNORECASE)


def preparation_effect_template(text, *, card_name, spell, compile_effect, **context):
    match = _SOURCE_PREPARATION.fullmatch(text.strip())
    if match is not None and (
        match["source"].casefold() in {"this creature", "this permanent"}
        or SourceReferenceSpec(card_name).matches(match["source"])
    ):
        effect = {"op": PREPARATION_RESULT_OPERATION, "schema_version": 1, "spell": spell.to_dict()}
        return "source-preparation-v1", (effect,), None, (PREPARATION_RESULT_MECHANIC,)
    return compile_effect(text, card_name=card_name, **context)


def preparation_entry_node(*,record,face_name,node_id,line,material_line,span,capability_registry,capability_profile,residuals):
    if material_line.strip().casefold() != "this creature enters prepared.":
        return None
    spell = preparation_spell(record, face_name)
    if spell is None:
        return None
    return runtime_handler_node(node_id=node_id,line=line,span=span,
        compiled=('preparation-enters-prepared-v1',{'handler_id':PREPARATION_HANDLER,'schema_version':1,
            'event':'zone.change','spell':spell.to_dict(),'requires_complete_card_program':True},PREPARATION_CAPABILITY),
        kind='static_ability',event='zone.change',active_zone='all',runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Prepared entry and its inset copy require complete source admission and the linked casting/lifetime contract.',
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
