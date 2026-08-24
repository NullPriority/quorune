from __future__ import annotations

"""Composition owner for typed modal trigger and activation blocks."""

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence

from .ir_model import OracleNode, OracleResidual, SourceSpan
from .modal_templates import fixed_nonrepeating_modal_template


_MODAL_BODY_SENTINEL = "__quorune_fixed_nonrepeating_modal_body__"
NodeCompiler = Callable[..., OracleNode | None]


@dataclass(frozen=True, slots=True)
class FixedModalContextBlocks:
    """Precompiled modal rows with source-order residual insertion."""

    nodes: Mapping[int, tuple[OracleNode, tuple[OracleResidual, ...]]]
    consumed_rows: frozenset[int]

    def append_to(
        self,
        row_index: int,
        *,
        nodes: list[OracleNode],
        residuals: list[OracleResidual],
    ) -> bool:
        if row_index in self.consumed_rows:
            return True
        result = self.nodes.get(row_index)
        if result is None:
            return False
        node, block_residuals = result
        residual_offset = len(residuals)
        remapped_ids = tuple(
            f"r{residual_offset + int(residual_id[1:])}"
            for residual_id in node.residual_ids
        )
        residuals.extend(
            replace(
                residual,
                residual_id=f"r{residual_offset + index}",
            )
            for index, residual in enumerate(block_residuals, 1)
        )
        nodes.append(replace(node, residual_ids=remapped_ids))
        return True


def _modal_block_end(
    material_rows: Sequence[tuple[str, str, SourceSpan]],
    start: int,
) -> int:
    end = start + 1
    while (
        end < len(material_rows)
        and material_rows[end][1].startswith("• ")
    ):
        end += 1
    return end


def _context_node(
    *,
    node_id: str,
    rows: Sequence[tuple[str, str, SourceSpan]],
    oracle_text: str,
    card_name: str,
    type_line: str,
    keywords: Sequence[str],
    trusted_mechanics: frozenset[str],
    capability_registry: Any,
    capability_profile: str,
    effect_template: Any,
    activated_or_event_node: NodeCompiler,
    trigger_node: NodeCompiler,
) -> tuple[OracleNode, tuple[OracleResidual, ...]] | None:
    modal = fixed_nonrepeating_modal_template(
        rows,
        compile_effect=lambda body: effect_template(
            body,
            card_name=card_name,
        ),
    )
    if modal is None or not modal.context_prefix:
        return None
    template, effects, target_schema, mechanics = modal.compiled()

    def modal_effect_template(
        body: str,
        **kwargs: Any,
    ) -> tuple[
        str | None,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        if body == _MODAL_BODY_SENTINEL:
            return template, effects, target_schema, mechanics
        return effect_template(body, **kwargs)

    synthetic_line = f"{modal.context_prefix}{_MODAL_BODY_SENTINEL}"
    span = SourceSpan(rows[0][2].start, rows[-1][2].end, rows[0][2].line)
    scratch_residuals: list[OracleResidual] = []
    node = activated_or_event_node(
        node_id=node_id,
        line=synthetic_line,
        material_line=synthetic_line,
        span=span,
        card_name=card_name,
        type_line=type_line,
        keywords=keywords,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=scratch_residuals,
        effect_template=modal_effect_template,
    )
    if node is None:
        node = trigger_node(
            node_id=node_id,
            line=synthetic_line,
            span=span,
            card_name=card_name,
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            residuals=scratch_residuals,
            effect_template=modal_effect_template,
        )
    if node is None or not node.lowerable:
        return None
    block_text = oracle_text[span.start : span.end]
    return (
        replace(node, text=block_text, span=span),
        tuple(
            replace(residual, text=block_text, span=span)
            for residual in scratch_residuals
        ),
    )


def fixed_nonrepeating_modal_context_blocks(
    *,
    material_rows: Sequence[tuple[str, str, SourceSpan]],
    face_id: str,
    oracle_text: str,
    card_name: str,
    type_line: str,
    keywords: Sequence[str],
    trusted_mechanics: frozenset[str],
    capability_registry: Any,
    capability_profile: str,
    effect_template: Any,
    activated_or_event_node: NodeCompiler,
    trigger_node: NodeCompiler,
) -> FixedModalContextBlocks:
    """Compile every disjoint header-plus-bullet modal context block."""

    nodes: dict[int, tuple[OracleNode, tuple[OracleResidual, ...]]] = {}
    consumed_rows: set[int] = set()
    for start in range(len(material_rows)):
        if start in consumed_rows:
            continue
        end = _modal_block_end(material_rows, start)
        if end - start < 3:
            continue
        result = _context_node(
            node_id=f"{face_id}:n{start + 1}",
            rows=material_rows[start:end],
            oracle_text=oracle_text,
            card_name=card_name,
            type_line=type_line,
            keywords=keywords,
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            effect_template=effect_template,
            activated_or_event_node=activated_or_event_node,
            trigger_node=trigger_node,
        )
        if result is None:
            continue
        nodes[start] = result
        consumed_rows.update(range(start + 1, end))
    return FixedModalContextBlocks(nodes, frozenset(consumed_rows))


__all__ = [
    "FixedModalContextBlocks",
    "fixed_nonrepeating_modal_context_blocks",
]
