"""Source-spanned compiler production for the closed one-X Harmonize price."""

from ..harmonize import DEPENDENCIES, EVENT, MECHANIC, HarmonizeSpec, harmonize_handler_descriptor
from ..util import mana_cost_to_vector, parse_mana_symbols
from . import regex_working_set as re
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


_PRICE = re.compile(r"Harmonize (?P<cost>(?:\{(?:X|[0-9]+|[WUBRGC])\})+)\.?", re.I)


def harmonize_keyword_node(*, record, node_id, line, material_line, span, capability_registry,
                           capability_profile, residuals, **_unused):
    match = _PRICE.fullmatch(material_line)
    if (
        match is None or record.layout != "normal" or record.faces
        or not {"instant", "sorcery"}.intersection(record.type_line.lower().split())
        or parse_mana_symbols(record.mana_cost).count("X") != 1
    ):
        return None
    cost = match["cost"].upper()
    if parse_mana_symbols(cost).count("X") != 1:
        return None
    vector, complex_symbols = mana_cost_to_vector(cost.replace("{X}", ""))
    if complex_symbols:
        return None
    price = HarmonizeSpec(vector)
    gate = explicit_capabilities_gate(
        DEPENDENCIES, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    blocked = (append_residual(
        residuals, kind="dependency_contract", text=line, span=span,
        reason="Fixed one-X Harmonize requires its graveyard cast, tap, and departure owners",
        blockers=gate.blockers,
    ),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="keyword_ability", text=line, span=span, active_zone="all",
        event=EVENT, lowerable=True, exact=not blocked, template_id="fixed-announced-x-harmonize-v1",
        handlers=(harmonize_handler_descriptor(price),), mechanics=(MECHANIC,), residual_ids=blocked,
        capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
