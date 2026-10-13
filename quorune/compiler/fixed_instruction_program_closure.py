from __future__ import annotations

"""Reviewed closure of separately owned fixed rule-action instructions."""

from ..rules.extra_turn_shapes import is_closed_fixed_extra_turn_program
from ..rules.connive_capability_shapes import is_closed_fixed_connive_program
from ..rules.goad_capability_shapes import is_closed_fixed_goad_program


def is_closed_fixed_instruction_program(program):
    return any(recognizer(program) for recognizer in (
        is_closed_fixed_extra_turn_program, is_closed_fixed_connive_program, is_closed_fixed_goad_program,
    ))
