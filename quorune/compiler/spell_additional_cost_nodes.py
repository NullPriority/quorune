from __future__ import annotations

"""Closed spell nodes whose mandatory additional cost is separately typed."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .dependency_gate import dependency_gate
from .ir_model import (
    OracleNode,
    OracleResidual,
    SourceSpan,
    append_residual,
)
from .spell_additional_cost_templates import (
    fixed_alternative_additional_cost_template,
    fixed_counter_additional_cost_template,
    fixed_tap_additional_cost_template,
    fixed_life_payment_additional_cost_template,
    fixed_sacrifice_additional_cost_template,
    fixed_zone_change_additional_cost_template,
)
from ..rules.capabilities import CapabilityRegistry
from .waterbend_costs import fixed_waterbend_additional_cost_template


EffectTemplate = Callable[
    ...,
    tuple[
        str | None,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ],
]
SourceRow = tuple[str, str, SourceSpan]
_ADDITIONAL_COST_PREFIX = "as an additional cost to cast this spell,"


def _combined_span(rows: Sequence[SourceRow]) -> SourceSpan:
    return SourceSpan(
        start=rows[0][2].start,
        end=rows[-1][2].end,
        line=rows[0][2].line,
    )


def _residual_spell_node(
    *,
    node_id: str,
    text: str,
    span: SourceSpan,
    residuals: list[OracleResidual],
    kind: str,
    reason: str,
    blockers: tuple[str, ...],
    cost: Mapping[str, Any] | None = None,
) -> OracleNode:
    residual_id = append_residual(
        residuals,
        kind=kind,
        text=text,
        span=span,
        reason=reason,
        blockers=blockers,
    )
    return _unlowerable_spell_node(
        node_id=node_id,
        text=text,
        span=span,
        residual_ids=(residual_id,),
        cost=cost,
    )


def _unlowerable_spell_node(
    *,
    node_id: str,
    text: str,
    span: SourceSpan,
    residual_ids: tuple[str, ...],
    cost: Mapping[str, Any] | None = None,
) -> OracleNode:
    return OracleNode(
        node_id=node_id,
        kind="spell_ability",
        text=text,
        span=span,
        active_zone="stack",
        event="resolve",
        lowerable=False,
        exact=False,
        cost=cost,
        residual_ids=residual_ids,
    )


def _unsupported_cost_result_residuals(
    *,
    rows: Sequence[SourceRow],
    card_name: str,
    effect_template: EffectTemplate,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> tuple[str, ...]:
    if len(rows) > 2:
        result_rows = rows[1:]
        return (
            append_residual(
                residuals,
                kind="spell_additional_cost",
                text="\n".join(row[0] for row in result_rows),
                span=_combined_span(result_rows),
                reason=(
                    "additional cost requires exactly one represented "
                    "spell-result clause"
                ),
                blockers=(
                    "additional-cost composition",
                    "ordered multi-clause spell resolution",
                ),
            ),
        )

    template, effects, target_schema, effect_mechanics = effect_template(
        rows[1][1],
        card_name=card_name,
    )
    result_text, _material_text, result_span = rows[1]
    if template is None:
        return (
            append_residual(
                residuals,
                kind="spell_effect",
                text=result_text,
                span=result_span,
                reason=(
                    "additional-cost spell has no exact generic "
                    "spell-result template"
                ),
                blockers=(),
            ),
        )

    mechanics = tuple(dict.fromkeys(effect_mechanics))
    gate = dependency_gate(
        mechanics=mechanics,
        effects=effects,
        target_schema=target_schema,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        cost_schema=None,
    )
    if not gate.blockers:
        return ()
    return (
        append_residual(
            residuals,
            kind="dependency_contract",
            text=result_text,
            span=result_span,
            reason=(
                "represented spell result depends on untrusted rules "
                "dependencies"
            ),
            blockers=gate.blockers,
        ),
    )


def _additional_cost_result_node(
    *,
    node_id: str,
    rows: Sequence[SourceRow],
    card_name: str,
    effect_template: EffectTemplate,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    cost: Any,
    cost_mechanics: tuple[str, ...],
    text: str,
    span: SourceSpan,
) -> OracleNode:
    template, effects, target_schema, effect_mechanics = effect_template(
        rows[1][1],
        card_name=card_name,
    )
    if template is None:
        residual_id = append_residual(
            residuals,
            kind="spell_effect",
            text=rows[1][0],
            span=rows[1][2],
            reason=(
                "typed additional cost has no exact generic "
                "spell-result template"
            ),
            blockers=(),
        )
        return _unlowerable_spell_node(
            node_id=node_id,
            text=text,
            span=span,
            residual_ids=(residual_id,),
            cost=cost.cost_schema,
        )
    mechanics = tuple(dict.fromkeys(
        ("cr-601-casting-spells", *cost_mechanics, *effect_mechanics)
    ))
    from ..waterbend import WATERBEND_COMPOSITION_MECHANIC
    if cost.cost_schema.get('additional_costs',[{}])[0].get('kind')=='waterbend':mechanics=(*mechanics,WATERBEND_COMPOSITION_MECHANIC)
    gate = dependency_gate(
        mechanics=mechanics,
        effects=effects,
        target_schema=target_schema,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        cost_schema=cost.cost_schema,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=text,
                span=span,
                reason=(
                    "lowerable additional-cost spell depends on untrusted "
                    "rules dependencies"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind="spell_ability",
        text=text,
        span=span,
        active_zone="stack",
        event="resolve",
        lowerable=True,
        exact=not gate.blockers,
        template_id=f"{cost.template_id}+{template}",
        cost=cost.cost_schema,
        effects=effects,
        target_schema=target_schema,
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(gate.closure.reachable if gate.closure else ()),
        capability_profile=(gate.closure.profile if gate.closure else None),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure else None
        ),
    )


def typed_additional_cost_spell_node(
    *,
    node_id: str,
    rows: Sequence[SourceRow],
    card_name: str,
    effect_template: EffectTemplate,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    """Compile or fail closed one represented mandatory additional cost."""

    if not rows:
        return None
    first_clause = rows[0][1].strip()
    cost = fixed_waterbend_additional_cost_template(first_clause) or fixed_tap_additional_cost_template(first_clause)
    cost_mechanics = ()
    if cost is None:
        cost=fixed_counter_additional_cost_template(first_clause)
        cost_mechanics=("cr-122-counters",) if cost is not None else ()
    if cost is None:
        cost = fixed_sacrifice_additional_cost_template(first_clause)
        cost_mechanics = ()
    if cost is None:
        cost = fixed_zone_change_additional_cost_template(first_clause, allow_counted=True)
        cost_mechanics = ()
    if cost is None:
        cost = fixed_life_payment_additional_cost_template(first_clause)
        cost_mechanics = ()
    if cost is None:
        cost = fixed_alternative_additional_cost_template(first_clause)
        cost_mechanics = ()
    if cost is None and not first_clause.casefold().startswith(
        _ADDITIONAL_COST_PREFIX
    ):
        return None
    text = "\n".join(row[0] for row in rows)
    span = _combined_span(rows)
    if cost is None:
        cost_blockers = (
            "typed spell additional-cost clause",
            *(
                (
                    "additional-cost composition",
                    "ordered multi-clause spell resolution",
                )
                if len(rows) == 1
                else ()
            ),
        )
        cost_residual_id = append_residual(
            residuals,
            kind="spell_additional_cost",
            text=rows[0][0],
            span=rows[0][2],
            reason="spell additional-cost grammar is outside the closed family",
            blockers=cost_blockers,
        )
        result_residual_ids = (
            _unsupported_cost_result_residuals(
                rows=rows,
                card_name=card_name,
                effect_template=effect_template,
                trusted_mechanics=trusted_mechanics,
                capability_registry=capability_registry,
                capability_profile=capability_profile,
                residuals=residuals,
            )
            if len(rows) >= 2
            else ()
        )
        return _unlowerable_spell_node(
            node_id=node_id,
            text=text,
            span=span,
            residual_ids=(cost_residual_id, *result_residual_ids),
        )
    if len(rows) != 2:
        return _residual_spell_node(
            node_id=node_id,
            text=text,
            span=span,
            residuals=residuals,
            kind="spell_additional_cost",
            reason=(
                "typed additional cost requires exactly one "
                "represented spell-result clause"
            ),
            blockers=(
                "additional-cost composition",
                "ordered multi-clause spell resolution",
            ),
            cost=cost.cost_schema,
        )
    return _additional_cost_result_node(
        node_id=node_id,
        rows=rows,
        card_name=card_name,
        effect_template=effect_template,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
        cost=cost,
        cost_mechanics=cost_mechanics,
        text=text,
        span=span,
    )


# Compatibility component name retained for the already-certified fixed-counter
# capability record. New compiler routing uses the family-level name above.
fixed_counter_additional_cost_spell_node = typed_additional_cost_spell_node


__all__ = [
    "fixed_counter_additional_cost_spell_node",
    "typed_additional_cost_spell_node",
]
