"""Lower one known spell result with a mana-value target bound to cast X."""

from __future__ import annotations

from dataclasses import replace

from ..rules.announced_mana_value_target import (
    announced_mana_value_capability,
    declare_announced_mana_value_limit,
    declare_own_graveyard_mana_value_limit,
    project_announced_target_program,
)
from . import regex_working_set as re
from .exile_templates import targeted_exile_effect_template
from .fixed_control_templates import fixed_control_effect_template


def announced_mana_value_target_template(text: str, *, has_cast_x: bool):
    if not has_cast_x:
        return None
    normalized = " ".join(text.strip().split())
    if re.fullmatch(
        r"return target creature card with mana value X or less from your graveyard to the battlefield\.?",
        normalized, re.IGNORECASE,
    ):
        from .reanimation_templates import fixed_target_reanimation_effect_template

        # The literal grammar supplies the result shape. The target binder still accepts X = 0.
        fixed = re.sub(r"mana value X", "mana value 1", normalized, flags=re.IGNORECASE)
        template = fixed_target_reanimation_effect_template(fixed)
        if template is None:
            return None
        name, effects, target, mechanics = template.compiled()
        return _TEMPLATE_PREFIX + name, effects, declare_own_graveyard_mana_value_limit(target), mechanics
    match = re.fullmatch(
        r"(?P<result>(?:Exile|Gain control of) target [^.]+?) with mana value X or less\.?",
        text.strip(), re.IGNORECASE,
    )
    if match is None:
        return None
    fixed = f"{match['result']} with mana value 0 or less."
    template = targeted_exile_effect_template(fixed) or fixed_control_effect_template(
        fixed, source_is_permanent=False,
    )
    if template is None:
        return None
    name, effects, target, mechanics = template.compiled()
    return (
        _TEMPLATE_PREFIX + name,
        effects,
        declare_announced_mana_value_limit(target),
        mechanics,
    )


_TEMPLATE_PREFIX = "announced-mana-value-target-v1+"


def announced_target_spell_compiler(fallback):
    def compile_spell(text: str, **context):
        return announced_mana_value_target_template(text, has_cast_x=True) or fallback(text, **context)
    return compile_spell


def closed_announced_mana_value_target_program(program, *, body_is_closed):
    """Require the typed target and the existing independently closed result."""
    if announced_mana_value_capability(program.target_schema) not in program.capability_dependencies:
        return False
    template = str(program.provenance.get("template_id") or "")
    if not template.startswith(_TEMPLATE_PREFIX):
        return False
    try:
        body = project_announced_target_program(program)
    except (TypeError, ValueError):
        return False
    return body_is_closed(replace(
        body, provenance={**body.provenance, "template_id": template.removeprefix(_TEMPLATE_PREFIX)},
    ))
