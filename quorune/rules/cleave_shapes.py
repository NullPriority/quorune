from __future__ import annotations

from ..spell_text_variants import SpellTextVariantKind
from .spell_text_variant_shapes import fixed_variant_program_is_closed


def fixed_cleave_program_is_closed(program):
    return fixed_variant_program_is_closed(program, SpellTextVariantKind.CLEAVE)
