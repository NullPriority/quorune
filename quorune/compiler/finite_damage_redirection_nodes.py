"""Compile the complete fixed one-point redirection body."""

from ..rules.finite_damage_redirection import (
    FINITE_DAMAGE_REDIRECTION_CAPABILITY,
    FINITE_DAMAGE_REDIRECTION_MECHANIC,
    FINITE_DAMAGE_REDIRECTION_TEMPLATE,
    finite_damage_redirection_instruction,
    finite_damage_redirection_node_capabilities,
    finite_damage_redirection_target,
    uses_finite_damage_redirection,
)
from .dependency_gate import DependencyGate, explicit_capabilities_gate


_BODY = (
    "the next 1 damage that would be dealt to this creature this turn "
    "is dealt to target creature you control instead"
)


def finite_damage_redirection_template(text):
    if text.strip().removesuffix(".").casefold() != _BODY:
        return None
    return (
        FINITE_DAMAGE_REDIRECTION_TEMPLATE,
        (finite_damage_redirection_instruction(),),
        finite_damage_redirection_target(),
        (FINITE_DAMAGE_REDIRECTION_MECHANIC,),
    )


def finite_damage_redirection_gate(*, effects, active_zone, cost_schema, target_schema, mechanics,
                                   capability_registry, capability_profile):
    if not any(uses_finite_damage_redirection(effect) for effect in effects):
        return None
    required = finite_damage_redirection_node_capabilities(
        effects=effects, target_schema=target_schema, mechanics=mechanics, active_zone=active_zone, cost=cost_schema,
    )
    if not required:
        return DependencyGate(blockers=(f"capability:{FINITE_DAMAGE_REDIRECTION_CAPABILITY}",))
    return explicit_capabilities_gate(
        required, capability_registry=capability_registry, capability_profile=capability_profile,
    )
