from __future__ import annotations

"""Capability closure for bounded programs of mandatory typed effects."""

from typing import Any, Iterable, Mapping, Sequence

from .fixed_effect_clause_shapes import (
    closed_effect_component_capabilities,
)
from .bound_effect_program_shapes import bound_effect_program_node_capabilities


CLOSED_EFFECT_PROGRAM_MECHANIC = "closed-effect-program"
CLOSED_EFFECT_PROGRAM_CAPABILITY = (
    "resolution.effect_program.closed_components"
)


def _contains_target_reference(value: Any) -> bool:
    if isinstance(value, str):
        return value in {"$target", "$targets"} or value.startswith("$target.")
    if isinstance(value, Mapping):
        return any(_contains_target_reference(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_target_reference(child) for child in value)
    return False


def closed_effect_program_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Own a bounded ordered program when every flattened effect is closed."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "bound-effect-program" in mechanics:
        return bound_effect_program_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        )
    if (
        CLOSED_EFFECT_PROGRAM_MECHANIC not in mechanics
        or not 2 <= len(effects) <= 8
    ):
        return ()
    target_effects = tuple(
        effect for effect in effects if _contains_target_reference(effect)
    )
    if target_schema is None:
        if target_effects:
            return ()
    elif not target_effects or target_schema.get("count") != 1:
        return ()

    component_mechanics = mechanics - {CLOSED_EFFECT_PROGRAM_MECHANIC}
    dependencies = {CLOSED_EFFECT_PROGRAM_CAPABILITY}
    if target_effects:
        for target_effect in target_effects:
            target_dependencies = closed_effect_component_capabilities((target_effect,),target_schema=target_schema,mechanics=component_mechanics)
            if not target_dependencies:return ()
            dependencies.update(target_dependencies)
    for effect in effects:
        if _contains_target_reference(effect):
            continue
        component = closed_effect_component_capabilities(
            (effect,),
            target_schema=None,
            mechanics=component_mechanics,
        )
        if not component:
            return ()
        dependencies.update(component)
    return tuple(sorted(dependencies))


def closed_effect_program_runtime_dependencies(program):
    """Keep the persisted ordered-program kind bound to its mutation owner."""
    schema=getattr(program,'target_schema',None)
    if isinstance(schema,Mapping) and isinstance(schema.get('modes'),Mapping):
        definitions=tuple(schema['modes'].values())
        marked=CLOSED_EFFECT_PROGRAM_MECHANIC in program.coverage or any(
            isinstance(branch,Mapping) and CLOSED_EFFECT_PROGRAM_MECHANIC in branch.get('mechanics',()) for branch in definitions)
        if not marked:return ()
        from ..compiler.modal_templates import FIXED_CHOOSE_ONE_MODAL_MECHANIC,FIXED_NONREPEATING_MODAL_MECHANIC
        from .modal_capability_shapes import FIXED_MODAL_WRAPPER_MECHANICS,fixed_choose_one_modal_branches,fixed_nonrepeating_modal_branches
        modal=FIXED_NONREPEATING_MODAL_MECHANIC if FIXED_NONREPEATING_MODAL_MECHANIC in program.coverage else FIXED_CHOOSE_ONE_MODAL_MECHANIC
        mechanics={modal,*set(program.coverage).intersection(FIXED_MODAL_WRAPPER_MECHANICS)}
        for definition in definitions:
            if not isinstance(definition,Mapping) or not isinstance(definition.get('mechanics'),(list,tuple)):
                raise ValueError('Modal ordered-program scope is malformed')
            mechanics.update(definition['mechanics'])
        decode=fixed_nonrepeating_modal_branches if FIXED_NONREPEATING_MODAL_MECHANIC in mechanics else fixed_choose_one_modal_branches
        branches=decode(effects=program.effects,target_schema=schema,mechanic_ids=tuple(sorted(mechanics)))
        if branches is None:raise ValueError('Modal ordered-program branches are malformed')
        from .modal_node_capabilities import fixed_modal_capability_dependencies
        modal_owned=fixed_modal_capability_dependencies(effects=program.effects,target_schema=schema,mechanics=tuple(sorted(mechanics)))
        if not modal_owned:raise ValueError('Modal ordered-program sibling branches are not independently closed')
        required=set()
        for branch in branches:
            if CLOSED_EFFECT_PROGRAM_MECHANIC not in branch.mechanics:continue
            owned=closed_effect_program_node_capabilities(effects=branch.effects,target_schema=branch.target_schema,mechanic_ids=branch.mechanics)
            if not owned:raise ValueError('Modal ordered-program branch lacks its complete ordered effect owner')
            required.update(owned)
        if CLOSED_EFFECT_PROGRAM_CAPABILITY not in required:raise ValueError('Modal ordered-program annotation has no owning branch')
        return tuple(sorted({*required,*modal_owned}))
    if CLOSED_EFFECT_PROGRAM_MECHANIC not in program.coverage:
        return ()
    if (len(program.effects) == 1
            and program.effects[0].get('op') == 'offer_optional_mana_payment'
            and program.effects[0].get('schema_version') == 2):
        from .fixed_effect_payment_shapes import fixed_effect_payment_node_capabilities
        owned=fixed_effect_payment_node_capabilities(effects=program.effects,
            target_schema=schema,mechanic_ids=program.coverage)
        if CLOSED_EFFECT_PROGRAM_CAPABILITY not in owned:
            raise ValueError('Paid ordered programs require a closed payment and complete child sequence')
        return owned
    if not 2 <= len(program.effects) <= 8:
        raise ValueError('Closed ordered programs require two to eight effects')
    return (CLOSED_EFFECT_PROGRAM_CAPABILITY,)


__all__ = [
    "CLOSED_EFFECT_PROGRAM_CAPABILITY",
    "CLOSED_EFFECT_PROGRAM_MECHANIC",
    "closed_effect_program_node_capabilities",
    "closed_effect_program_runtime_dependencies",
]
