from __future__ import annotations

"""Authority of closed mandatory single-stack-target changes."""

from collections.abc import Mapping

from ..replacement.immutable import thaw_value
from ..stack_target_change_model import (
    MANDATORY_STACK_TARGET_CHANGE_FIELD,
    OPTIONAL_STACK_TARGET_CHANGE_FIELD,
    SINGLE_ANNOUNCED_STACK_TARGET,
    STACK_TARGET_CHANGE_FIELDS,
    STACK_SPELL_CONTROL_FIELD,
    OptionalStackTargetChangeSpec,
    SingleStackTargetChangeSpec,
    StackSpellControlSpec,
    is_stack_target_change_payment,
    validate_stack_spell_control_effect,
    validate_stack_target_change_payment_effect,
)
from .effect_shape_walkers import nested_effects
from .stack_spell_copy_shapes import fixed_stack_spell_copy_node_capabilities
from .source_directed_redirect_shapes import (
    source_directed_redirect_node_capabilities, source_directed_redirect_program_is_closed,
    source_directed_redirect_runtime_dependencies,
)


def _stack_spell_control_capabilities(effects, target_schema):
    try:
        validate_stack_spell_control_effect(effects[0])
    except (TypeError, ValueError):
        return ()
    schema = thaw_value(target_schema)
    values = tuple(thaw_value(effects))
    for noncreature_only in (False, True):
        spec = StackSpellControlSpec(noncreature_only)
        if values == spec.effects and schema == spec.target_schema:
            return ("continuous.control.fixed_resolution", "target.revalidate_resolution",
                    "trigger.event.normalized_target_announcement")
    return ()


def _stack_target_change_payment_capabilities(effect, target_schema):
    try:
        validate_stack_target_change_payment_effect(effect)
    except (TypeError, ValueError):
        return ()
    if thaw_value(target_schema) != SingleStackTargetChangeSpec(False).target_schema:
        return ()
    return ("effect.choice.optional_fixed_mana_payment", "target.revalidate_resolution",
            "trigger.event.normalized_target_announcement")


def single_stack_target_change_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) == 1 and is_stack_target_change_payment(effects[0]):
        return _stack_target_change_payment_capabilities(effects[0], target_schema)
    if len(effects) == 2 and STACK_SPELL_CONTROL_FIELD in effects[0]:
        return _stack_spell_control_capabilities(effects, target_schema)
    if len(effects) != 1 or not STACK_TARGET_CHANGE_FIELDS.intersection(effects[0]):
        return ()
    effect = thaw_value(effects[0])
    schema = thaw_value(target_schema)
    for marker, model in (
        (MANDATORY_STACK_TARGET_CHANGE_FIELD, SingleStackTargetChangeSpec),
        (OPTIONAL_STACK_TARGET_CHANGE_FIELD, OptionalStackTargetChangeSpec),
    ):
        if type(effect.get(marker)) is not int:
            continue
        for spell_or_ability in (False, True):
            spec = model(spell_or_ability)
            if (effect,) == spec.effects and schema == spec.target_schema:
                return ("target.revalidate_resolution", "trigger.event.normalized_target_announcement")
    return ()


STACK_TARGET_EFFECT_RESOLVERS = (
    fixed_stack_spell_copy_node_capabilities,
    single_stack_target_change_capabilities,
    source_directed_redirect_node_capabilities,
)


def single_stack_target_change_runtime_dependencies(program):
    if source_redirect := source_directed_redirect_runtime_dependencies(program):
        return source_redirect
    schema = program.target_schema
    effects = (*program.effects, *getattr(program, "handlers", ()))
    marked = any(STACK_SPELL_CONTROL_FIELD in effect or STACK_TARGET_CHANGE_FIELDS.intersection(effect)
                 or is_stack_target_change_payment(effect) for effect in nested_effects(effects))
    if not marked and (not isinstance(schema, Mapping) or schema.get("predicate") != SINGLE_ANNOUNCED_STACK_TARGET):
        return ()
    if (program.event, program.active_zone) != ("resolve", "stack") or getattr(program, "handlers", ()):
        raise ValueError("Stack target change requires one complete resolving spell")
    from ..card_programs.admission import program_has_declared_complete_source_admission

    if not program_has_declared_complete_source_admission(program):
        raise ValueError("Stack target change requires exact whole-source admission")
    required = single_stack_target_change_capabilities(
        effects=program.effects, target_schema=schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Stack target change requires the exact original stack domain and one instruction")
    return required


def single_stack_target_change_program_is_closed(program):
    if source_directed_redirect_program_is_closed(program):
        return True
    from ..card_programs.admission import program_has_declared_complete_source_admission

    if not program_has_declared_complete_source_admission(program):
        return False
    required = single_stack_target_change_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required).issubset(program.capability_dependencies)
