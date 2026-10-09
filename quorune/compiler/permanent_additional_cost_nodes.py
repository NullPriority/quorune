from __future__ import annotations

"""Mandatory casting-price declarations for permanent spells."""

from typing import Any
from dataclasses import replace

from .dependency_gate import dependency_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual
from .spell_additional_cost_templates import (
    fixed_alternative_additional_cost_template,
    fixed_counter_additional_cost_template,
    fixed_life_payment_additional_cost_template,
    fixed_sacrifice_additional_cost_template,
    fixed_zone_change_additional_cost_template,
)
from ..rules.capabilities import capability_dependencies_for_node


PERMANENT_ADDITIONAL_COST_TEMPLATE = "fixed-permanent-additional-cost-v1"
PERMANENT_ADDITIONAL_COST_COVERAGE = "fixed-permanent-additional-cost-declaration"


def permanent_additional_cost_node(*, node_id, line, material_line, span: SourceSpan,
    card_types, capability_registry, capability_profile, residuals: list[OracleResidual]):
    if not set(card_types).intersection({"artifact", "battle", "creature", "enchantment", "planeswalker"}):
        return None
    if not material_line.casefold().startswith("as an additional cost to cast this spell,"):
        return None
    template = next((value for parse in (
        fixed_counter_additional_cost_template, fixed_sacrifice_additional_cost_template,
        fixed_zone_change_additional_cost_template, fixed_life_payment_additional_cost_template,
        fixed_alternative_additional_cost_template,
    ) if (value := parse(material_line)) is not None), None)
    if template is None:
        return None
    mechanics = ("cr-601-casting-spells",)
    gate = dependency_gate(mechanics=mechanics, effects=(), target_schema=None,
        cost_schema=template.cost_schema, trusted_mechanics=frozenset(),
        capability_registry=capability_registry, capability_profile=capability_profile)
    residual_ids = ()
    if gate.blockers:
        residual_ids = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
            reason="Permanent casting price requires its existing typed cost capabilities", blockers=gate.blockers),)
    return OracleNode(node_id=node_id, kind="spell_ability", text=line, span=span,
        active_zone="stack", event="resolve", lowerable=True, exact=not gate.blockers,
        template_id=PERMANENT_ADDITIONAL_COST_TEMPLATE, cost=template.cost_schema,
        mechanics=mechanics, runtime_coverage=(PERMANENT_ADDITIONAL_COST_COVERAGE,),
        residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None)


def is_closed_permanent_additional_cost_program(program: Any) -> bool:
    if (program.provenance.get("template_id") != PERMANENT_ADDITIONAL_COST_TEMPLATE
        or PERMANENT_ADDITIONAL_COST_COVERAGE not in program.coverage
        or program.effects or program.handlers or program.target_schema is not None
        or program.event_condition is not None or program.event != "resolve"
        or program.active_zone != "stack" or program.destination != "battlefield"
        or not program.ability_id.startswith("spell:")):
        return False
    dependencies = capability_dependencies_for_node(effects=(), target_schema=None,
        mechanic_ids=("cr-601-casting-spells",), cost_schema=program.cost_schema)
    return bool(dependencies) and set(dependencies) <= set(program.capability_dependencies)


def reject_repeated_permanent_additional_costs(nodes, residuals):
    prices = [index for index, node in enumerate(nodes) if node.template_id == PERMANENT_ADDITIONAL_COST_TEMPLATE]
    if len(prices) <= 1:
        return tuple(nodes)
    result = list(nodes)
    for index in prices:
        node = result[index]
        residual = append_residual(residuals, kind="spell_additional_cost", text=node.text, span=node.span,
            reason="Repeated permanent prices require a shared casting-cost composition boundary",
            blockers=("multiple mandatory casting-price declarations",))
        result[index] = replace(node, exact=False, lowerable=False, residual_ids=(*node.residual_ids, residual))
    return tuple(result)


__all__ = [
    "PERMANENT_ADDITIONAL_COST_TEMPLATE", "PERMANENT_ADDITIONAL_COST_COVERAGE",
    "permanent_additional_cost_node", "is_closed_permanent_additional_cost_program",
    "reject_repeated_permanent_additional_costs",
]
