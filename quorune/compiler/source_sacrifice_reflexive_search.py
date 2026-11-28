"""Lower one exact self-entry sacrifice and its separate basic-land search trigger."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..source_sacrifice_model import fixed_source_sacrifice_dependencies, reflexive_search_sacrifice_effect
from ..rules.source_sacrifice_reflexive_search import MECHANICS, child_program
from . import regex_working_set as re
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual
from .library_search_templates import fixed_library_search_effect_template


_LINE = re.compile(
    r"When this land enters, sacrifice it\. When you do, (?P<search>search your library for "
    r"a basic [A-Za-z]+, [A-Za-z]+, or [A-Za-z]+ card, put it onto the battlefield tapped, then shuffle)"
    r" and you gain 1 life\.", re.I,
)


def source_sacrifice_reflexive_search_node(
    *, node_id, line, material_line, span, capability_registry, capability_profile, residuals,
):
    match = _LINE.fullmatch(" ".join(material_line.split()))
    if match is None:
        return None
    try:
        search = fixed_library_search_effect_template(match["search"] + ".")
        if search is None:
            return None
        _, (search_effect,), _, _ = search.compiled()
        effect = reflexive_search_sacrifice_effect(child_program(search_effect))
        required = fixed_source_sacrifice_dependencies(effect)
    except (TypeError, ValueError):
        return None
    gate = explicit_capabilities_gate(
        required, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    blocked = (append_residual(
        residuals, kind="dependency_contract", text=line, span=span,
        reason="Reflexive source sacrifice requires its original search and life owners",
        blockers=gate.blockers,
    ),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span, active_zone="battlefield",
        event="permanent.enter.self", lowerable=True, exact=not blocked,
        template_id="source-entry-sacrifice-reflexive-search-v1", effects=(effect,),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,), mechanics=MECHANICS,
        residual_ids=blocked, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
