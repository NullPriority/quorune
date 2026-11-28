"""Compose a granted tapped owner return and the independently executable Treasure."""

from dataclasses import replace

from . import regex_working_set as re
from .dependency_gate import explicit_capabilities_gate
from .token_templates import fixed_token_creation_effect_template
from ..rules.granted_death_treasure import CHILD_TEMPLATE, granted_death_treasure_effect_dependencies

_DEATH_TREASURE = re.compile(
    r"(When this creature dies, return it to the battlefield tapped under its owner['’]s control) "
    r"and you create a Treasure token\.",
    re.IGNORECASE,
)


def granted_death_treasure_base_text(quoted):
    match = _DEATH_TREASURE.fullmatch(quoted)
    return match[1] + "." if match is not None else None


def compose_granted_death_treasure(inner, *, capability_registry, capability_profile):
    token = fixed_token_creation_effect_template("Create a Treasure token.")
    if token is None:
        return None
    effects = (*inner.effects, token.effect)
    required = granted_death_treasure_effect_dependencies(effects)
    if not required:
        return None
    gate = explicit_capabilities_gate(tuple(sorted({*inner.capability_dependencies, *required})),
                                      capability_registry=capability_registry, capability_profile=capability_profile)
    if gate.blockers:
        return None
    return replace(
        inner, effects=effects, template_id=CHILD_TEMPLATE, mechanics=(*inner.mechanics, *token.mechanics),
        capability_dependencies=gate.capabilities, capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
    )
