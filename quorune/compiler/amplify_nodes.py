from __future__ import annotations

"""Source-spanned fixed Amplify keyword productions."""

import re

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.amplify import AMPLIFY_CAPABILITY, AMPLIFY_HANDLER, AmplifySpec
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, SourceSpan, append_residual


_FIXED_AMPLIFY = r"Amplify [0-9]+"


def amplify_keyword_nodes(*, node_id, line, material_line, span, capability_registry, capability_profile, residuals):
    if not re.fullmatch(rf"{_FIXED_AMPLIFY}(?:,\s*{_FIXED_AMPLIFY})*\.?", material_line.strip(), re.I):
        return None
    parts = re.findall(r"Amplify ([0-9]+)", material_line, re.I)
    spans = list(re.finditer(r"\bAmplify\s+([0-9]+)\b", line, re.I))
    if parts != [part[1] for part in spans]:
        return None
    gate = explicit_capabilities_gate((AMPLIFY_CAPABILITY, "counter.placement.quantity_replacement"),
        capability_registry=capability_registry, capability_profile=capability_profile)
    nodes = []
    for index, part in enumerate(spans):
        source, source_span = line, span
        if len(parts) > 1:
            source = part[0]
            source_span = SourceSpan(span.start + part.start(), span.start + part.end(), span.line)
        spec = AmplifySpec(int(part[1]))
        blocked = (append_residual(residuals, kind="dependency_contract", text=source, span=source_span,
            reason="Amplify requires the private entry choice and counter capabilities", blockers=gate.blockers),
        ) if gate.blockers else ()
        nodes.append(OracleNode(
            node_id=node_id if len(parts) == 1 else f"{node_id}:amplify:{index}",
            kind="static_ability", text=source, span=source_span, active_zone="all", event="zone.change",
            lowerable=True, exact=not gate.blockers, template_id="amplify-entry-v1",
            handlers=({"handler_id": AMPLIFY_HANDLER, "schema_version": 1, "event": "zone.change",
                "amplify": spec.to_dict()},),
            runtime_coverage=("typed_amplify_entry", CURRENT_ABILITY_FRAGMENT_COVERAGE),
            residual_ids=blocked, capability_dependencies=gate.capabilities,
            capability_closure=gate.closure.reachable if gate.closure else (),
            capability_profile=gate.closure.profile if gate.closure else None,
            capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
        ))
    return tuple(nodes)
