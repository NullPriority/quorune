from __future__ import annotations

"""Compatibility entry points for the shared paid-text program boundary."""

from ..spell_text_variants import SpellTextVariantKind
from .spell_text_variant_shapes import fixed_variant_program_is_closed, variant_program_has_complete_admission


def overload_program_has_complete_admission(program):
    return variant_program_has_complete_admission(program, SpellTextVariantKind.OVERLOAD)


def fixed_overload_program_is_closed(program):
    return fixed_variant_program_is_closed(program, SpellTextVariantKind.OVERLOAD)
