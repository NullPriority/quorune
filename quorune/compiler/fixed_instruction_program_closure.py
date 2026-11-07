from __future__ import annotations

"""Reviewed closure of separately owned fixed rule-action instructions."""

from ..rules.extra_turn_shapes import is_closed_fixed_extra_turn_program
from ..rules.scheduled_turn_skip_shapes import is_closed_scheduled_turn_skip_program
from ..rules.earthbend_shapes import is_closed_earthbend_program
from ..rules.suspect_shapes import is_closed_suspect_program
from ..rules.connive_capability_shapes import is_closed_fixed_connive_program
from ..rules.endure_shapes import is_closed_fixed_endure_program
from ..rules.blight_shapes import is_closed_fixed_blight_program
from ..rules.goad_capability_shapes import is_closed_fixed_goad_program
from ..rules.exploit_capability_shapes import is_closed_exploit_instruction_program
from ..rules.created_token_counter_shapes import is_closed_created_token_counter_program
from ..rules.incubate_capability_shapes import is_closed_incubate_program
from ..rules.discover_capability_shapes import is_closed_discover_program
from ..rules.variable_library_selection_shapes import is_closed_variable_library_selection_program
from ..rules.reveal_until_library_selection_shapes import is_closed_reveal_until_library_selection_program
from ..rules.detain import is_closed_permanent_action_prohibition_program


def is_closed_fixed_instruction_program(program):
    return any(recognizer(program) for recognizer in (
        is_closed_created_token_counter_program,
        is_closed_incubate_program,
        is_closed_fixed_endure_program,
        is_closed_fixed_blight_program,
        is_closed_discover_program,
        is_closed_permanent_action_prohibition_program,
        is_closed_variable_library_selection_program,
        is_closed_reveal_until_library_selection_program,
        is_closed_fixed_extra_turn_program, is_closed_fixed_connive_program, is_closed_fixed_goad_program,
        is_closed_scheduled_turn_skip_program,
        is_closed_earthbend_program,
        is_closed_suspect_program,
        is_closed_exploit_instruction_program,
    ))
