from __future__ import annotations

"""Reviewed composition of closed, typed effect templates."""

from typing import Any, Callable, Mapping
from functools import partial

from .closed_effect_programs import closed_effect_program_template
from .bound_effect_programs import bound_effect_program_template
from .fixed_effect_clause_sequences import fixed_effect_clause_sequence_template
from .public_query_effect_amounts import public_query_effect_amount_template
from .declared_effect_amounts import declared_effect_amount_template
from .fixed_resolution_characteristics import fixed_resolution_characteristics_effect_template
from .fixed_effect_payment_templates import fixed_effect_payment_template,fixed_effect_payment_with_mandatory_prefix
from .optional_payment_templates import fixed_optional_mana_payment_template


CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
EffectCompiler = Callable[[str], CompiledEffectTemplate]


def reviewed_contextual_effect_template(
    text: str, *, card_name: str,
    compile_atomic: Callable[..., CompiledEffectTemplate],
    compile_fixed: Callable[..., CompiledEffectTemplate],
    cast_x_available: bool = False, forbid_public_x: bool = False,
    **source_context: Any,
) -> CompiledEffectTemplate:
    """Bind source context before composing the same closed leaf owners."""

    def atomic_with_characteristics(body: str) -> CompiledEffectTemplate:
        current = compile_atomic(body, card_name=card_name, **source_context)
        if current[0] is not None:
            return current
        return fixed_resolution_characteristics_effect_template(
            body,
            source_name=card_name,
            source_is_permanent=source_context.get("source_is_permanent"),
            source_card_types=tuple(source_context.get("source_card_types", ())),
        ) or current

    # Preserve the certified v1 one-mana/one-leaf payload before using v2.
    legacy_payment=fixed_optional_mana_payment_template(text,compile_effect=atomic_with_characteristics)
    if legacy_payment is not None:
        from ..rules.fixed_effect_clause_shapes import fixed_optional_mana_payment_node_capabilities
        if fixed_optional_mana_payment_node_capabilities(effects=legacy_payment.effects,target_schema=legacy_payment.target_schema,mechanic_ids=legacy_payment.mechanics):
            return legacy_payment.compiled()
    def payment_body(body: str) -> CompiledEffectTemplate:
        return reviewed_effect_template_composition(body,source_name=card_name,
            compile_atomic=atomic_with_characteristics,
            compile_fixed=partial(compile_fixed,card_name=card_name,**source_context))
    fixed_payment=fixed_effect_payment_template(text,compile_effect=payment_body) or fixed_effect_payment_with_mandatory_prefix(text,compile_effect=payment_body)
    if fixed_payment is not None:
        return fixed_payment
    return reviewed_effect_template_composition(
        text, source_name=card_name,
        cast_x_available=cast_x_available, forbid_public_x=forbid_public_x,
        compile_atomic=atomic_with_characteristics,
        compile_fixed=partial(compile_fixed,card_name=card_name,**source_context),
    )


def reviewed_effect_template_composition(
    text: str,
    *,
    source_name: str,
    compile_atomic: EffectCompiler,
    compile_fixed: EffectCompiler,
    cast_x_available: bool = False,
    forbid_public_x: bool = False,
    allow_declarations: bool = True,
) -> CompiledEffectTemplate:
    """Compile one reviewed effect or a closed composition of those effects."""

    atomic = compile_atomic(text)
    if atomic[0] is not None:
        return atomic
    query_amount = public_query_effect_amount_template(
        text, source_name=source_name, compile_fixed=compile_fixed,
    )
    if query_amount is not None:
        return query_amount
    if allow_declarations:
        declared = declared_effect_amount_template(
            text, source_name=source_name, cast_x_available=cast_x_available,
            forbid_public_x=forbid_public_x,
            compile_fixed=lambda body: reviewed_effect_template_composition(
                body, source_name=source_name, compile_atomic=compile_atomic,
                compile_fixed=compile_fixed, allow_declarations=False,
            ),
        )
        if declared is not None:
            return declared
    sequence = fixed_effect_clause_sequence_template(
        text,
        compile_clause=compile_atomic,
    )
    if sequence is not None:
        return sequence.compiled()
    bound = bound_effect_program_template(text, compile_component=compile_atomic)
    if bound is not None:
        return bound.compiled()
    program = closed_effect_program_template(
        text,
        compile_component=compile_atomic,
    )
    return program.compiled() if program is not None else atomic
