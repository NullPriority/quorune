from __future__ import annotations

"""Compose a printed trigger-use limit with an independently closed trigger."""

from dataclasses import replace
import re
from typing import Any, Callable, Mapping

from ..rules.trigger_limits import TriggerLimitSpec, TRIGGER_ONCE_PER_TURN_CAPABILITY
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .dependency_gate import dependency_gate
from .ir_model import OracleNode, append_residual

_LIMIT = re.compile(r"(?P<body>.+) This ability triggers only once each turn\.", re.IGNORECASE)
_UNSUPPORTED_EVENTS = frozenset({
    "unresolved", "resolve", "activate", "multi.event", "artifact.graveyard",
    "permanent.graveyard", "permanent.leave", "permanent.leave.self",
    "creature.dies", "creature.dies.self", "spell.countered",
})


def limited_trigger_node(*, line: str, material_line: str, compile_inner: Callable[..., OracleNode | None],
                         arguments: Mapping[str, Any]) -> OracleNode | None:
    match = _LIMIT.fullmatch(material_line)
    if match is None:
        return None
    inner_arguments = dict(arguments)
    private_residuals = []
    inner_arguments.update(line=match["body"], material_line=match["body"], residuals=private_residuals)
    node = compile_inner(**inner_arguments)
    if node is None or not node.exact or node.kind != "triggered_ability" or node.active_zone != "battlefield" or node.event in _UNSUPPORTED_EVENTS:
        return None
    gate = dependency_gate(mechanics=node.mechanics, effects=node.effects, target_schema=node.target_schema,
        trusted_mechanics=arguments["trusted_mechanics"], capability_registry=arguments["capability_registry"],
        capability_profile=arguments["capability_profile"], explicit_capabilities=(*node.capability_dependencies,TRIGGER_ONCE_PER_TURN_CAPABILITY))
    residual_ids = ()
    if gate.blockers:
        residual_ids = (append_residual(arguments["residuals"],kind="dependency_contract",text=line,span=node.span,
            reason="trigger use limit depends on untrusted capability contracts",blockers=gate.blockers),)
    closure = gate.closure
    return replace(node,text=line,trigger_limit=TriggerLimitSpec().to_dict(),exact=not gate.blockers,
        runtime_coverage=tuple(dict.fromkeys((*node.runtime_coverage,CURRENT_ABILITY_FRAGMENT_COVERAGE))),
        residual_ids=residual_ids,capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)
