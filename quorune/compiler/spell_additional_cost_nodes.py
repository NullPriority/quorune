from __future__ import annotations

"""Closed spell nodes whose mandatory additional cost is separately typed."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from . import regex_working_set as re
from .dependency_gate import dependency_gate
from .ir_model import (
    OracleNode,
    OracleResidual,
    SourceSpan,
    append_residual,
)
from .spell_additional_cost_templates import (
    FixedZoneChangeAdditionalCostTemplate,
    fixed_alternative_additional_cost_template, announced_x_additional_cost_template,
    fixed_counter_additional_cost_template,
    fixed_tap_additional_cost_template,
    fixed_life_payment_additional_cost_template,
    fixed_sacrifice_additional_cost_template,
    fixed_zone_change_additional_cost_template,
)
from ..rules.capabilities import CapabilityRegistry
from ..rules.casting_additional_costs import fixed_creature_sacrifice_price
from ..scalar_effect_amount_model import ScalarAmountOrigin, scalar_amount_specs
from .paid_sacrifice_amount_templates import paid_sacrifice_amount_template
from .paid_creature_search_templates import paid_creature_search_template
from .paid_revealed_creature_templates import (
    mandatory_creature_reveal_template, paid_revealed_creature_gate, paid_revealed_creature_template,
)
from .paid_sacrifice_condition_templates import paid_sacrifice_condition_template
from .colored_hand_credit_template import colored_hand_credit_template
from .creature_sacrifice_credit import creature_sacrifice_credit_template
from .mandatory_selected_zone_costs import fixed_discard_sacrifice_template
from .waterbend_costs import fixed_waterbend_additional_cost_template
from .random_discard_spell_costs import fixed_random_discard_spell_cost_template


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
_QUOTED_AMOUNT_SEPARATOR = re.compile(r',"(?=\s+where X is )', re.I)


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
    def compile_fixed(body):
        return effect_template(body, card_name=card_name)

    from .paid_discard_damage_templates import paid_nonland_discard_damage_template

    paid = paid_nonland_discard_damage_template(
        rows[1][1], cost.cost_schema, compile_fixed=compile_fixed,
    ) or paid_creature_search_template(
        rows[1][1], cost_schema=cost.cost_schema, source_name=card_name,
    ) or paid_revealed_creature_template(
        rows[1][1], cost_schema=cost.cost_schema, source_name=card_name,
    ) or paid_sacrifice_amount_template(
        rows[1][1], cost_schema=cost.cost_schema, source_name=card_name,
        compile_fixed=compile_fixed,
    ) or paid_sacrifice_condition_template(
        rows[1][1], cost_schema=cost.cost_schema, compile_fixed=compile_fixed,
    )
    template, effects, target_schema, effect_mechanics = paid or effect_template(rows[1][1], card_name=card_name)
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
    dependency_context = dict(
        mechanics=mechanics,
        effects=effects,
        target_schema=target_schema,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        cost_schema=cost.cost_schema,
    )
    gate = paid_revealed_creature_gate(**dependency_context) or dependency_gate(
        **dependency_context, trusted_mechanics=trusted_mechanics,
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


def _damage_qualified_sacrifice_spell_cost(text: str) -> FixedZoneChangeAdditionalCostTemplate | None:
    from ..rules.casting_additional_costs import (
        DAMAGE_QUALIFIED_SACRIFICE_VERSION, SACRIFICE_ONE_COST, damage_qualified_sacrifice_predicate,
    )

    normalized = " ".join(text.split()).casefold()
    if normalized != "as an additional cost to cast this spell, sacrifice a creature that dealt damage this turn.":
        return None
    return FixedZoneChangeAdditionalCostTemplate(
        SACRIFICE_ONE_COST, damage_qualified_sacrifice_predicate(), schema_version=DAMAGE_QUALIFIED_SACRIFICE_VERSION,
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
    """Compile or fail closed one represented additional cost."""

    if not rows:
        return None
    first_clause = rows[0][1].strip()
    from .paid_sacrifice_copy_templates import paid_sacrifice_copy_cost_template
    from ..rules.paid_sacrifice_copy import MECHANIC as PAID_SACRIFICE_COPY_MECHANIC

    paid_copy = paid_sacrifice_copy_cost_template(first_clause)
    cost = (
        paid_copy or _damage_qualified_sacrifice_spell_cost(first_clause)
        or colored_hand_credit_template(first_clause) or creature_sacrifice_credit_template(first_clause)
        or fixed_discard_sacrifice_template(first_clause)
        or fixed_random_discard_spell_cost_template(first_clause) or announced_x_additional_cost_template(first_clause)
        or fixed_waterbend_additional_cost_template(first_clause) or fixed_tap_additional_cost_template(first_clause)
    )
    cost_mechanics = (PAID_SACRIFICE_COPY_MECHANIC,) if paid_copy is not None else ()
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
        cost = mandatory_creature_reveal_template(first_clause) or fixed_alternative_additional_cost_template(first_clause)
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


def paid_sacrifice_token_ability_nodes(
    *, record, face_id, rows, card_name, effect_template, trusted_mechanics,
    capability_registry, capability_profile, compile_inner, trigger_node, grant_effect_templates,
) -> tuple[OracleNode, ...] | None:
    """Bind a paid token count before compiling the token's intrinsic abilities."""
    if len(rows) != 2 or '"' not in rows[1][1]:
        return None
    cost = fixed_sacrifice_additional_cost_template(rows[0][1])
    if cost is None or not fixed_creature_sacrifice_price(cost.cost_schema):
        return None
    from .typed_token_ability_nodes import typed_token_ability_nodes

    result_line, material, result_span = rows[1]
    node_id = f"{face_id}:n1"
    full_text = "\n".join(row[0] for row in rows)

    def compile_spell_shell(shell):
        outer = _additional_cost_result_node(
            node_id=node_id, rows=(rows[0], (result_line, shell, result_span)),
            card_name=card_name, effect_template=effect_template, trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry, capability_profile=capability_profile,
            residuals=[], cost=cost, cost_mechanics=(), text=full_text, span=_combined_span(rows),
        )
        if not outer.exact or outer.target_schema is not None or len(outer.effects) != 1:
            return None
        effect, = outer.effects
        amounts = scalar_amount_specs(effect.get("quantity"))
        if effect.get("op") != "create_token" or len(amounts) != 1:
            return None
        amount, = amounts
        if amount.origin is not ScalarAmountOrigin.PAID_SACRIFICE_CREATURE:
            return None
        return (outer,)

    return typed_token_ability_nodes(
        record=record, face_id=face_id, node_id=node_id, line=result_line,
        material_line=_QUOTED_AMOUNT_SEPARATOR.sub('.",', material),
        span=result_span, source_name=card_name, effect_template=effect_template,
        keywords=(), printed_card_types=(), source_attachment_relation=None,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, compile_inner=compile_inner,
        trigger_node=trigger_node, grant_effect_templates=grant_effect_templates,
        compile_spell_shell=compile_spell_shell,
    )


# Compatibility component name retained for the already-certified fixed-counter
# capability record. New compiler routing uses the family-level name above.
fixed_counter_additional_cost_spell_node = typed_additional_cost_spell_node


__all__ = [
    "fixed_counter_additional_cost_spell_node",
    "typed_additional_cost_spell_node",
]
