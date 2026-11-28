from __future__ import annotations

"""CardProgram nodes for closed static runtime-handler productions."""

from typing import AbstractSet, Any, Mapping

from ..rules.capabilities import CapabilityRegistry
from ..object_predicate import object_query_capabilities
from ..legend_rule_model import legend_rule_payload_dependencies
from ..static_player_restriction_model import static_player_restriction_capabilities
from ..conditional_recipient_model import conditional_recipient_capabilities
from ..public_zone_condition_model import public_zone_condition_capabilities
from ..public_history_query_model import public_history_query_dependencies
from ..crime_model import crime_fact_dependencies
from ..rules.conditional_free_cast_shapes import conditional_free_cast_capabilities
from ..characteristic_fragments import characteristic_quantity_capabilities
from .dependency_gate import dependency_gate, explicit_capabilities_gate
from .ir_model import append_residual, OracleNode, OracleResidual, SourceSpan
from .runtime_templates import static_runtime_template


_DAMAGEABLE_CARD_TYPES = frozenset({"battle", "creature", "planeswalker"})


def runtime_handler_node(
    *,
    node_id: str,
    line: str,
    span: SourceSpan,
    compiled: tuple[
        str,
        Mapping[str, Any] | tuple[Mapping[str, Any], ...],
        str | tuple[str, ...],
    ],
    kind: str,
    event: str,
    active_zone: str = "battlefield",
    runtime_coverage: tuple[str, ...] = (),
    mechanics: tuple[str, ...] = (),
    dependency_reason: str,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode:
    """Lower one already-compiled runtime descriptor into a guarded node."""

    template_id, raw_handlers, capabilities = compiled
    handlers = (
        raw_handlers
        if isinstance(raw_handlers, tuple)
        else (raw_handlers,)
    )
    required = (*((capabilities,) if isinstance(capabilities, str) else capabilities),
                *characteristic_quantity_capabilities(handlers), *object_query_capabilities(handlers),
                *static_player_restriction_capabilities(handlers), *conditional_recipient_capabilities(handlers),
                *public_zone_condition_capabilities(handlers), *conditional_free_cast_capabilities(handlers),
                *public_history_query_dependencies(handlers),*crime_fact_dependencies(handlers),
                *legend_rule_payload_dependencies(handlers))
    if mechanics:
        gate = dependency_gate(mechanics=mechanics, effects=(), target_schema=None,
            trusted_mechanics=frozenset(), capability_registry=capability_registry,
            capability_profile=capability_profile, explicit_capabilities=required)
    else:
        gate = explicit_capabilities_gate(required, capability_registry=capability_registry,
            capability_profile=capability_profile)
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=dependency_reason,
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind=kind,
        text=line,
        span=span,
        active_zone=active_zone,
        event=event,
        lowerable=True,
        exact=not gate.blockers,
        template_id=template_id,
        handlers=handlers,
        runtime_coverage=runtime_coverage,
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure is not None else (),
        capability_profile=closure.profile if closure is not None else None,
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


def static_runtime_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    source_name: str,
    card_types: AbstractSet[str],
    permanent_card_types: AbstractSet[str],
    source_is_class: bool,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    """Lower one closed static descriptor and its capability evidence."""

    from ..commander_pairing import (
        COMMANDER_PAIRING_EVENT,
        PRINTED_COMMANDER_ELIGIBILITY_CAPABILITY,
        PRINTED_COMMANDER_ELIGIBILITY_COVERAGE,
        PRINTED_COMMANDER_ELIGIBILITY_TEMPLATE,
    )
    from .commander_pairing_nodes import has_printed_commander_permission

    if (
        not source_is_class
        and "planeswalker" in card_types
        and has_printed_commander_permission(material_line, source_name=source_name)
    ):
        return runtime_handler_node(
            node_id=node_id, line=line, span=span,
            compiled=(PRINTED_COMMANDER_ELIGIBILITY_TEMPLATE, (), PRINTED_COMMANDER_ELIGIBILITY_CAPABILITY),
            kind="static_ability", event=COMMANDER_PAIRING_EVENT, active_zone="all",
            runtime_coverage=(PRINTED_COMMANDER_ELIGIBILITY_COVERAGE,),
            dependency_reason="Printed commander eligibility requires its trusted setup authority",
            capability_registry=capability_registry, capability_profile=capability_profile,
            residuals=residuals,
        )
    from .devotion_templates import fixed_devotion_increase_handler
    from .token_templates import static_token_creation_replacement_handler
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from .legend_rule_templates import static_legend_rule_exemption_handler
    legend = (static_legend_rule_exemption_handler(material_line)
        if not source_is_class and card_types.intersection(permanent_card_types) else None)
    if legend is not None:
        return runtime_handler_node(node_id=node_id, line=line, span=span, compiled=legend,
            kind='static_ability', event='continuous',
            runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
            dependency_reason='Legend-rule exemptions require their current static ability and state-based owner',
            capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
    from .crew_nodes import crew_power_bonus_handler
    crew_power = (crew_power_bonus_handler(material_line)
        if not source_is_class and card_types.intersection(permanent_card_types) else None)
    if crew_power is not None:
        return runtime_handler_node(node_id=node_id, line=line, span=span, compiled=crew_power,
            kind='static_ability', event='characteristics.evaluate',
            runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
            dependency_reason='Crew power bonuses require their shared cost owner',
            capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
    from .counter_permission_templates import static_counter_prohibition_handler
    from ..counter_permission import COUNTER_PROHIBITION_EVENT
    counter_prohibition = (static_counter_prohibition_handler(material_line, source_name=source_name)
        if not source_is_class and card_types.intersection(permanent_card_types) else None)
    if counter_prohibition is not None:
        return runtime_handler_node(node_id=node_id, line=line, span=span, compiled=counter_prohibition,
            kind='static_ability', event=COUNTER_PROHIBITION_EVENT,
            runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
            dependency_reason='Counter placement requires its typed current prohibition owner',
            capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
    from .damage_permission_templates import static_damage_unpreventability_handler
    from ..damage_prevention_permission import UNPREVENTABLE_EVENT
    unpreventable = (static_damage_unpreventability_handler(material_line)
        if not source_is_class and card_types.intersection(permanent_card_types) else None)
    if unpreventable is not None:
        return runtime_handler_node(node_id=node_id,line=line,span=span,compiled=unpreventable,
            kind='static_ability',event=UNPREVENTABLE_EVENT,runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
            dependency_reason='Static unpreventable damage requires its typed permission owner',
            capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    from .life_templates import (
        static_life_gain_prohibition_handler,
        static_opponent_life_gain_to_loss_handler,
    )
    from ..life_gain_permission import LIFE_GAIN_PROHIBITION_EVENT
    prohibition = (static_life_gain_prohibition_handler(material_line)
        if not source_is_class and card_types.intersection(permanent_card_types) else None)
    if prohibition is not None:
        return runtime_handler_node(node_id=node_id,line=line,span=span,compiled=prohibition,
            kind='static_ability',event=LIFE_GAIN_PROHIBITION_EVENT,runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
            dependency_reason='Static life-change permission requires its typed rules owner',
            capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    life_conversion = (
        static_opponent_life_gain_to_loss_handler(material_line)
        if not source_is_class and card_types.intersection(permanent_card_types) else None
    )
    if life_conversion is not None:
        return runtime_handler_node(
            node_id=node_id, line=line, span=span, compiled=life_conversion,
            kind="replacement_effect", event="life.change", runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
            dependency_reason="Opposing life-gain conversion requires its typed current replacement owner",
            capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
        )
    devotion = (fixed_devotion_increase_handler(material_line)
        if not source_is_class and card_types.intersection(permanent_card_types) else None)
    if devotion is not None:
        return runtime_handler_node(node_id=node_id,line=line,span=span,compiled=devotion,
            kind='static_ability',event='devotion.evaluate',runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
            dependency_reason='Devotion requires its current public-query capability',
            capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    token = (static_token_creation_replacement_handler(material_line)
        if not source_is_class and card_types.intersection(permanent_card_types) else None)
    if token is not None:
        return runtime_handler_node(node_id=node_id,line=line,span=span,compiled=token,
            kind='replacement_effect',event='token.create',runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
            dependency_reason='Token creation replacement requires its canonical replacement owner',
            capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    from .static_recipient_qualifiers import recipient_qualified_static_handler
    qualified = (recipient_qualified_static_handler(material_line)
        if not source_is_class and card_types.intersection(permanent_card_types) else None)
    if qualified is not None:
        return runtime_handler_node(node_id=node_id, line=line, span=span, compiled=qualified,
            kind='static_ability', event='characteristics.evaluate',
            runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
            dependency_reason='Recipient qualifications require the live public query and characteristic owners',
            capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
    template = static_runtime_template(
        material_line,
        source_name=source_name,
        source_damageable=bool(card_types.intersection(_DAMAGEABLE_CARD_TYPES)),
        source_permanent=bool(card_types.intersection(permanent_card_types)),
        source_is_class=source_is_class,
        capability_profile=capability_profile,
    )
    if template is None:
        return None
    return runtime_handler_node(
        node_id=node_id,
        line=line,
        span=span,
        compiled=template.compiled,
        kind=template.kind,
        event=template.event,
        active_zone=template.active_zone,
        runtime_coverage=template.runtime_coverage,
        mechanics=template.mechanics,
        dependency_reason=template.dependency_reason,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )


__all__ = ["runtime_handler_node", "static_runtime_node"]
