from __future__ import annotations

from ...spell_text_variants import SpellTextVariantKind
from .fixed_text_variant_costs import (
    fixed_variant_cost_options, select_variant_program, select_variant_proposal_program, validate_variant_commit,
)


def cleave_cost_options(host, seat, card, program, **options):
    return fixed_variant_cost_options(host, seat, card, program, kind=SpellTextVariantKind.CLEAVE, **options)


def select_cleave_cast_program(host, card, program, option):
    return select_variant_program(host, card, program, option, SpellTextVariantKind.CLEAVE)


def select_cleave_proposal_program(host, card, program, option):
    return select_variant_proposal_program(host, card, program, option, SpellTextVariantKind.CLEAVE)


def validate_cleave_commit(host, card, program, option):
    return validate_variant_commit(host, card, program, option, SpellTextVariantKind.CLEAVE)
