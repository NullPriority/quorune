from __future__ import annotations

"""Closed source/body checks shared by represented spell-text alternatives."""

from ..spell_text_variants import SpellTextVariantKind, spell_body_fingerprint


def variant_spec(kind, value):
    if kind is SpellTextVariantKind.OVERLOAD:
        from ..overload_model import FixedManaOverloadSpec
        return FixedManaOverloadSpec.from_dict(value)
    if kind is SpellTextVariantKind.CLEAVE:
        from ..cleave_model import FixedManaCleaveSpec
        return FixedManaCleaveSpec.from_dict(value)
    raise ValueError('Unrepresented spell-text variant')


def variant_program_has_complete_admission(program, kind):
    from ..card_programs.admission import CARD_PROGRAM_ADMISSION_FIELD, CompleteCardProgramAdmission
    if program is None or kind.boundary not in program.coverage:
        return False
    try:
        return CompleteCardProgramAdmission.from_dict(program.provenance[CARD_PROGRAM_ADMISSION_FIELD]).admitted
    except (KeyError, TypeError, ValueError):
        return False


def fixed_variant_program_is_closed(program, kind):
    coverage = set(program.coverage)
    if kind.boundary not in coverage:
        return False
    try:
        if set(program.cost_schema or {}) != {kind.value}:
            return False
        spec = variant_spec(kind, program.cost_schema[kind.value])
    except (ValueError, TypeError, KeyError):
        return False
    role = coverage.intersection({kind.normal_role, kind.paid_role})
    if role == {kind.normal_role}:
        expected = spec.normal_fingerprint
        if program.ability_id != 'spell:front':
            return False
        if kind is SpellTextVariantKind.OVERLOAD and program.target_schema is None:
            return False
    elif role == {kind.paid_role}:
        expected = spec.overloaded_fingerprint if kind is SpellTextVariantKind.OVERLOAD else spec.cleaved_fingerprint
        if program.ability_id != 'spell:front:' + kind.value:
            return False
        if kind is SpellTextVariantKind.OVERLOAD and program.target_schema is not None:
            return False
    else:
        return False
    if program.target_schema is not None and 'modes' in program.target_schema:
        return False
    if spell_body_fingerprint(program.effects, program.target_schema) != expected:
        return False
    from .capabilities import capability_dependencies_for_node
    mechanics = coverage - {'generated_oracle_ir', 'spell_resolution', kind.value, kind.boundary,
        kind.normal_role, kind.paid_role}
    required = capability_dependencies_for_node(effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=mechanics)
    return bool(required) and set(required).issubset(program.capability_dependencies) and kind.capability in program.capability_dependencies
