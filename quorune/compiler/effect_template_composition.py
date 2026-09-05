from __future__ import annotations

"""Reviewed composition of closed, typed effect templates."""

from typing import Any, Callable, Mapping

from .closed_effect_programs import closed_effect_program_template
from .fixed_effect_clause_sequences import fixed_effect_clause_sequence_template
from .public_query_effect_amounts import public_query_effect_amount_template


CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
EffectCompiler = Callable[[str], CompiledEffectTemplate]


def reviewed_effect_template_composition(
    text: str,
    *,
    source_name: str,
    compile_atomic: EffectCompiler,
    compile_fixed: EffectCompiler,
) -> CompiledEffectTemplate:
    """Compile one reviewed effect or a closed composition of those effects."""

    atomic = compile_atomic(text)
    if atomic[0] is not None:
        return atomic
    query_amount = public_query_effect_amount_template(
        text,
        source_name=source_name,
        compile_fixed=compile_fixed,
    )
    if query_amount is not None:
        return query_amount
    sequence = fixed_effect_clause_sequence_template(
        text,
        compile_clause=compile_atomic,
    )
    if sequence is not None:
        return sequence.compiled()
    program = closed_effect_program_template(
        text,
        compile_component=compile_atomic,
    )
    return program.compiled() if program is not None else atomic
