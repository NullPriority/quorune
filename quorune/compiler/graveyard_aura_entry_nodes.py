"""Recognize a complete graveyard Aura return bound to a creature's entry."""

from . import regex_working_set as re
from ..rules.graveyard_aura_entry import (
    GRAVEYARD_AURA_ENTRY_CAPABILITIES, GRAVEYARD_AURA_ENTRY_TEMPLATE,
    graveyard_aura_entry_condition, optional_graveyard_aura_entry,
)
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


_AURA_ENTRY = re.compile(
    r"When(?:ever)? a creature with mana value (?P<minimum>[1-9][0-9]*) or greater enters, "
    r"you may return this card from your graveyard to the battlefield attached to that creature\.?$",
    re.I,
)


def graveyard_aura_entry_node(*, node_id, line, material_line, span, type_line,
                             capability_registry, capability_profile, residuals):
    match = _AURA_ENTRY.fullmatch(material_line)
    if match is None or re.fullmatch(r"(?:Legendary )?Enchantment — Aura", type_line, re.I) is None:
        return None
    gate = explicit_capabilities_gate(GRAVEYARD_AURA_ENTRY_CAPABILITIES,
                                      capability_registry=capability_registry, capability_profile=capability_profile)
    residual_ids = (append_residual(
        residuals, kind="dependency_contract", text=line, span=span,
        reason="Event-bound Aura entry requires the closed optional source movement and attachment owners",
        blockers=gate.blockers,
    ),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span, active_zone="graveyard",
        event="permanent.enter", lowerable=True, exact=not gate.blockers, template_id=GRAVEYARD_AURA_ENTRY_TEMPLATE,
        event_condition=graveyard_aura_entry_condition(int(match["minimum"])), effects=(optional_graveyard_aura_entry(),),
        residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure is not None else (),
        capability_profile=closure.profile if closure is not None else None,
        capability_fingerprint=closure.fingerprint if closure is not None else None,
    )
