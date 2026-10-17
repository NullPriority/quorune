from __future__ import annotations

"""Stable Overload entry points over shared fixed text-variant cost ownership."""

from ...spell_text_variants import SpellTextVariantKind
from .fixed_text_variant_costs import (
    fixed_variant_cost_options, normal_variant_program, paid_variant_program,
    select_variant_program, select_variant_proposal_program, validate_variant_commit,
)


def normal_overload_program(host, card, program):
    return normal_variant_program(host, card, program, SpellTextVariantKind.OVERLOAD)


def overload_cost_options(host, seat, card, program, **options):
    return fixed_variant_cost_options(host, seat, card, program, kind=SpellTextVariantKind.OVERLOAD, **options)


def _paid_overload_program(host, normal, spec):
    return paid_variant_program(host, normal, spec, SpellTextVariantKind.OVERLOAD)


def select_overload_cast_program(host, card, program, option):
    return select_variant_program(host, card, program, option, SpellTextVariantKind.OVERLOAD)


def validate_overload_commit(host, card, program, option):
    return validate_variant_commit(host, card, program, option, SpellTextVariantKind.OVERLOAD)


def select_overload_proposal_program(host, card, program, option):
    return select_variant_proposal_program(host, card, program, option, SpellTextVariantKind.OVERLOAD)
