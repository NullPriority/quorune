from __future__ import annotations

"""Retain existing leaf closure while correlating the two Overload bodies."""

from ..overload_model import (
    FixedManaOverloadSpec, OVERLOAD_CAPABILITY, OVERLOAD_FIELD, OVERLOAD_BOUNDARY,
    OVERLOAD_NORMAL, OVERLOAD_PAID, overload_body_fingerprint,
)


def overload_program_has_complete_admission(program):
    """Read the generated whole-card certificate for this non-handler family."""
    from ..card_programs.admission import CARD_PROGRAM_ADMISSION_FIELD, CompleteCardProgramAdmission
    if program is None or OVERLOAD_BOUNDARY not in program.coverage:
        return False
    try:
        return CompleteCardProgramAdmission.from_dict(program.provenance[CARD_PROGRAM_ADMISSION_FIELD]).admitted
    except (KeyError, TypeError, ValueError):
        return False


def fixed_overload_program_is_closed(program):
    coverage = set(program.coverage)
    if OVERLOAD_BOUNDARY not in coverage:
        return False
    try:
        if set(program.cost_schema or {}) != {OVERLOAD_FIELD}:
            return False
        spec = FixedManaOverloadSpec.from_dict(program.cost_schema[OVERLOAD_FIELD])
    except (ValueError, TypeError, KeyError):
        return False
    role = coverage.intersection({OVERLOAD_NORMAL, OVERLOAD_PAID})
    if role == {OVERLOAD_NORMAL}:
        expected = spec.normal_fingerprint
        if program.ability_id != 'spell:front' or program.target_schema is None:
            return False
    elif role == {OVERLOAD_PAID}:
        expected = spec.overloaded_fingerprint
        if program.ability_id != 'spell:front:overload' or program.target_schema is not None:
            return False
    else:
        return False
    if overload_body_fingerprint(program.effects, program.target_schema) != expected:
        return False
    from .capabilities import capability_dependencies_for_node
    mechanics = coverage - {'generated_oracle_ir', 'spell_resolution', 'overload', OVERLOAD_BOUNDARY,
        OVERLOAD_NORMAL, OVERLOAD_PAID}
    required = capability_dependencies_for_node(effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=mechanics)
    return bool(required) and set(required).issubset(program.capability_dependencies) and OVERLOAD_CAPABILITY in program.capability_dependencies
