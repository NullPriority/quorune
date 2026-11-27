"""Lower one known spell result with a mana-value target bound to cast X."""

from __future__ import annotations

from dataclasses import replace

from ..rules.announced_mana_value_target import (
    announced_mana_value_capability,
    declare_activation_mana_value_equality, declare_activation_graveyard_mana_value_limit,
    exact_artifact_destruction_price, exact_land_return_x_activation_price,
    declare_announced_mana_value_limit,
    declare_own_graveyard_mana_value_limit,
    distinct_graveyard_reanimation_effect,
    distinct_own_graveyard_mana_value_schema,
    project_announced_target_program,
)
from . import regex_working_set as re
from .exile_templates import targeted_exile_effect_template
from .fixed_control_templates import fixed_control_effect_template


def _distinct_graveyard_return_template(text):
    if re.fullmatch(
        r"Return from your graveyard to the battlefield any number of target creature cards "
        r"that each have a different mana value X or less[.]?",
        text, re.IGNORECASE,
    ) is None:
        return None
    from .fixed_homogeneous_target_sets import FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC
    from .reanimation_templates import FIXED_TARGET_REANIMATION_MECHANIC, FIXED_TARGET_REANIMATION_SET_MECHANIC

    return (
        _TEMPLATE_PREFIX + "fixed-graveyard-reanimation-set-v1",
        (distinct_graveyard_reanimation_effect(),),
        distinct_own_graveyard_mana_value_schema(),
        (FIXED_TARGET_REANIMATION_MECHANIC, FIXED_TARGET_REANIMATION_SET_MECHANIC,
         FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC, "cr-115-targets"),
    )


def announced_mana_value_target_template(text: str, *, has_cast_x: bool):
    if not has_cast_x:
        return None
    normalized = " ".join(text.strip().split())
    distinct = _distinct_graveyard_return_template(normalized)
    if distinct is not None:
        return distinct
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


def _activation_graveyard_return_template(ability, text):
    if (ability.zones != ("battlefield",) or not ability.sorcery_speed or ability.activation_conditions
            or re.fullmatch(r"Return target artifact or enchantment card with mana value X or less "
                            r"from your graveyard to the battlefield[.]?", text.strip(), re.I) is None):
        return None
    from .activated_costs import activated_ability_cost
    from .reanimation_templates import fixed_target_reanimation_effect_template

    if not exact_land_return_x_activation_price(activated_ability_cost(ability)):
        return None
    template = fixed_target_reanimation_effect_template(
        "Return target artifact or enchantment card with mana value 1 or less from your graveyard to the battlefield.")
    if template is None:
        return None
    name, effects, target, mechanics = template.compiled()
    return _TEMPLATE_PREFIX + name, effects, declare_activation_graveyard_mana_value_limit(target), mechanics


def announced_activation_mana_value_target_template(ability, text: str):
    if graveyard := _activation_graveyard_return_template(ability, text):
        return graveyard
    if (ability.zones != ("battlefield",) or ability.sorcery_speed or ability.activation_conditions
            or re.fullmatch(r"Destroy target noncreature artifact with mana value X[.]?", text.strip(), re.I) is None):
        return None
    from .activated_costs import activated_ability_cost
    from .destruction_templates import targeted_destruction_effect_template

    if not exact_artifact_destruction_price(activated_ability_cost(ability)):
        return None
    template = targeted_destruction_effect_template("Destroy target noncreature artifact with mana value 0.")
    if template is None:
        return None
    name, effects, target, mechanics = template.compiled()
    return _TEMPLATE_PREFIX + name, effects, declare_activation_mana_value_equality(target), mechanics


_TEMPLATE_PREFIX = "announced-mana-value-target-v1+"


def announced_target_spell_compiler(fallback, *, distinct_graveyard_only=False):
    def compile_spell(text: str, **context):
        if distinct_graveyard_only:
            return _distinct_graveyard_return_template(" ".join(text.strip().split())) or fallback(text, **context)
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
