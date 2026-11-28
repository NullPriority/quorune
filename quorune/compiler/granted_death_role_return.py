"""Compose a sealed self-death grant with the predefined Wicked Role children."""

from dataclasses import replace
from . import regex_working_set as re

from ..rules.granted_death_role_return import (
    granted_death_role_effect, granted_death_role_payload_dependencies,
)
from .role_token_nodes import role_definition_children


_DEATH_ROLE = re.compile(
    r"(When this creature dies, return it to the battlefield tapped under its owner['’]s control), "
    r"then create a Wicked Role token attached to it\.", re.I,
)


def granted_death_role_base_text(quoted):
    match = _DEATH_ROLE.fullmatch(quoted)
    return match[1] + "." if match is not None else None


def compose_granted_death_role_inner(inner, *, record, face_id, node_id, source_text, span,
                                     capability_registry, capability_profile):
    if ((inner.event, inner.active_zone) != ("creature.dies.self", "battlefield")
            or inner.target_schema is not None or capability_registry is None):
        return None
    definition = role_definition_children(role="Wicked", record=record, face_id=face_id,
        node_id=node_id, line=source_text, span=span, capability_registry=capability_registry,
        capability_profile=capability_profile)
    if definition is None:
        return None
    fragments, children = definition
    effect = granted_death_role_effect(fragments)
    dependencies = tuple(sorted({
        *inner.capability_dependencies, *granted_death_role_payload_dependencies(effect),
        *(capability for child in children for capability in child.capability_dependencies),
    }))
    closure = capability_registry.closure(dependencies, profile=capability_profile)
    if not closure.trusted:
        return None
    return replace(inner, effects=(effect,),
        template_id="granted-death-tapped-return-then-wicked-role-v1",
        capability_dependencies=dependencies, capability_closure=closure.reachable,
        capability_profile=closure.profile, capability_fingerprint=closure.fingerprint), children
