from __future__ import annotations

from . import main_phase_combats
from .paid_mana_spell_faces import compile_cast_fact_spell_face


def compile_contextual_spell_face(*, record, effect_template, **face_arguments):
    """Lower a complete spell whose instructions depend on a rules context."""
    return main_phase_combats.compile_main_phase_combat_face(**face_arguments) or compile_cast_fact_spell_face(
        record=record, effect_template=effect_template, **face_arguments,
    )
