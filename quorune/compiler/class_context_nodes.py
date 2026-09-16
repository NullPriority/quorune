from __future__ import annotations

"""Compile ordinary CR 716 Class structure around typed child abilities."""

from dataclasses import dataclass, replace
import re
from typing import Any, Iterable, Mapping, Sequence

from ..ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE,
    STATIC_COMPONENT_SCOPE_FRAGMENT_HANDLER_ID,
    StaticComponentApplicabilitySpec,
    StaticComponentScopeSpec,
    ability_fragment_to_dict,
)
from ..class_levels import (
    CLASS_LIFECYCLE_CAPABILITY_ID,
    CLASS_MECHANIC_ID,
    CLASS_REMINDER_TEXT,
    FixedClassLevelAbilitySpec,
    class_level_handler_descriptor,
)
from ..replacement.immutable import FrozenMap
from ..util import mana_cost_to_vector
from .activated_costs import activated_ability_cost
from .dependency_gate import explicit_capabilities_gate
from .generated_program_identity import generated_ability_id
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


CLASS_REMINDER_TEMPLATE_ID = "class-lifecycle-reminder-v1"
CLASS_LEVEL_ACTIVATION_TEMPLATE_ID = "class-level-activation-v1"
CLASS_LEVEL_SCOPE_TEMPLATE_ID = "class-level-static-scope-v1"
_LEVEL_LINE = re.compile(
    r"^(?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+):\s*"
    r"Level\s+(?P<level>[23])$",
    re.IGNORECASE,
)
_LEVEL_SIGNAL = re.compile(r":\s*Level\s+\d+\s*$", re.IGNORECASE)
_CYCLE_SENSITIVE_FRAGMENT_KINDS = frozenset(
    {
        "dynamic_power_toughness",
        "query_characteristic_modifier",
        "query_power_toughness_definition",
    }
)


@dataclass(frozen=True, slots=True)
class ParsedClassLevel:
    level: int
    cost_text: str
    mana_cost: FrozenMap
    activation_row: int
    member_lines: frozenset[int]


@dataclass(frozen=True, slots=True)
class ParsedClassContext:
    reminder_row: int
    levels: tuple[ParsedClassLevel, ParsedClassLevel]

    @property
    def consumed_rows(self) -> frozenset[int]:
        return frozenset(
            (self.reminder_row, *(level.activation_row for level in self.levels))
        )


def parse_class_context(
    *,
    layout: str,
    type_line: str,
    material_rows: Sequence[tuple[str, str, SourceSpan]],
) -> ParsedClassContext | None:
    """Recognize exactly one reminder and the two ordinary Class level bars."""

    normalized_type = type_line.replace("—", "-").casefold()
    if (
        layout != "class"
        or "- class" not in normalized_type
        or len(material_rows) < 6
        or material_rows[0][0] != CLASS_REMINDER_TEXT
        or material_rows[0][1]
    ):
        return None
    matched_rows = tuple(
        (index, _LEVEL_LINE.fullmatch(material_line))
        for index, (_line, material_line, _span) in enumerate(material_rows)
        if _LEVEL_LINE.fullmatch(material_line) is not None
    )
    signaled_rows = tuple(
        index
        for index, (_line, material_line, _span) in enumerate(material_rows)
        if _LEVEL_SIGNAL.search(material_line) is not None
    )
    if (
        len(matched_rows) != 2
        or signaled_rows != tuple(index for index, _match in matched_rows)
        or tuple(int(match.group("level")) for _index, match in matched_rows)
        != (2, 3)
        or matched_rows[0][0] <= 1
        or matched_rows[1][0] <= matched_rows[0][0] + 1
        or matched_rows[1][0] >= len(material_rows) - 1
    ):
        return None
    parsed: list[ParsedClassLevel] = []
    for position, (activation_row, match) in enumerate(matched_rows):
        cost_text = match.group("cost").upper()
        mana_cost, complex_symbols = mana_cost_to_vector(cost_text)
        if complex_symbols:
            return None
        end = (
            matched_rows[position + 1][0]
            if position + 1 < len(matched_rows)
            else len(material_rows)
        )
        member_lines = frozenset(
            material_rows[index][2].line
            for index in range(activation_row + 1, end)
        )
        if not member_lines:
            return None
        parsed.append(
            ParsedClassLevel(
                level=int(match.group("level")),
                cost_text=cost_text,
                mana_cost=FrozenMap(mana_cost),
                activation_row=activation_row,
                member_lines=member_lines,
            )
        )
    return ParsedClassContext(0, (parsed[0], parsed[1]))


def class_compilation_rows(
    context: ParsedClassContext | None,
    rows: Sequence[tuple[str, str, SourceSpan]],
) -> Iterable[tuple[int, tuple[str, str, SourceSpan]]]:
    consumed = context.consumed_rows if context is not None else frozenset()
    return (
        (index, row)
        for index, row in enumerate(rows, 1)
        if index - 1 not in consumed
    )


def _is_static_declaration(node: OracleNode) -> bool:
    return bool(
        node.handlers
        or (
            node.kind == "keyword_ability"
            and node.capability_dependencies
        )
    )


def _semantic_key(
    *, oracle_id: str, face_id: str, node: OracleNode
) -> str | None:
    ability_id = generated_ability_id(
        kind=node.kind,
        face_id=face_id,
        line=node.span.line,
        static_declaration=_is_static_declaration(node),
        node_id=node.node_id,
    )
    return f"{oracle_id}:{ability_id}" if ability_id is not None else None


def _uses_cycle_sensitive_fragment(node: OracleNode) -> bool:
    return any(
        isinstance(fragment, Mapping)
        and fragment.get("kind") in _CYCLE_SENSITIVE_FRAGMENT_KINDS
        for descriptor in node.handlers
        for fragment in (descriptor.get("fragment"),)
    )


def _unsupported_dynamic_child(
    node: OracleNode, residuals: list[OracleResidual]
) -> OracleNode:
    return replace(
        node,
        exact=False,
        handlers=(),
        runtime_coverage=(),
        mechanics=tuple(dict.fromkeys((*node.mechanics, CLASS_MECHANIC_ID))),
        residual_ids=(
            *node.residual_ids,
            append_residual(
                residuals,
                kind="unsupported_class_dynamic_child",
                text=node.text,
                span=node.span,
                reason=(
                    "Class-level dynamic characteristics require a cycle-safe "
                    "applicability boundary"
                ),
                blockers=("cycle-safe Class-level dynamic characteristic",),
            ),
        ),
    )


def _with_class_dependency(
    node: OracleNode,
    *,
    capability_registry: Any,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode:
    gate = explicit_capabilities_gate(
        (*node.capability_dependencies, CLASS_LIFECYCLE_CAPABILITY_ID),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = node.residual_ids
    if gate.blockers:
        residual_ids = (
            *residual_ids,
            append_residual(
                residuals,
                kind="dependency_contract",
                text=node.text,
                span=node.span,
                reason="Class ability depends on untrusted level semantics",
                blockers=gate.blockers,
            ),
        )
    return replace(
        node,
        exact=node.exact and not gate.blockers,
        mechanics=tuple(dict.fromkeys((*node.mechanics, CLASS_MECHANIC_ID))),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
    )


def _keyword_members(
    node: OracleNode, *, printed_keywords: Sequence[str]
) -> tuple[str, ...]:
    if node.kind != "keyword_ability":
        return ()
    by_normalized = {
        keyword.casefold(): keyword for keyword in printed_keywords
    }
    return tuple(
        sorted(
            {
                by_normalized[mechanic.casefold()]
                for mechanic in node.mechanics
                if mechanic.casefold() in by_normalized
            },
            key=str.casefold,
        )
    )


def _scope_descriptor(
    *,
    parent_semantic_key: str,
    child_semantic_keys: tuple[str, ...],
    keywords: tuple[str, ...],
    minimum_level: int,
) -> dict[str, Any]:
    return {
        "handler_id": STATIC_COMPONENT_SCOPE_FRAGMENT_HANDLER_ID,
        "schema_version": 1,
        "event": "characteristics.evaluate",
        "fragment": ability_fragment_to_dict(
            StaticComponentScopeSpec(
                parent_semantic_key=parent_semantic_key,
                child_semantic_keys=child_semantic_keys,
                keywords=keywords,
                applicability=StaticComponentApplicabilitySpec(
                    kind="source_numeric_designation_at_least",
                    designation="class_level",
                    minimum=minimum_level,
                ),
            )
        ),
    }


def _reminder_node(
    *,
    face_id: str,
    row: tuple[str, str, SourceSpan],
    capability_registry: Any,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode:
    line, _material, span = row
    gate = explicit_capabilities_gate(
        (CLASS_LIFECYCLE_CAPABILITY_ID,),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason="Class reminder depends on untrusted lifecycle semantics",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return OracleNode(
        node_id=f"{face_id}:class-reminder",
        kind="reminder_text",
        text=line,
        span=span,
        active_zone="all",
        event="none",
        lowerable=True,
        exact=not gate.blockers,
        template_id=CLASS_REMINDER_TEMPLATE_ID,
        mechanics=(CLASS_MECHANIC_ID,),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
    )


def _level_node(
    *,
    face_id: str,
    parsed: ParsedClassLevel,
    row: tuple[str, str, SourceSpan],
    capability_registry: Any,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode:
    line, _material, span = row
    spec = FixedClassLevelAbilitySpec(
        ability_id=f"ab{span.line}",
        line_index=span.line - 1,
        oracle_line=line,
        cost_text=parsed.cost_text,
        mana_cost=parsed.mana_cost,
        level=parsed.level,
    )
    gate = explicit_capabilities_gate(
        (CLASS_LIFECYCLE_CAPABILITY_ID,),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason="Class activation depends on untrusted lifecycle semantics",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return OracleNode(
        node_id=f"{face_id}:class-level:{parsed.level}",
        kind="activated_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="activate",
        lowerable=True,
        exact=not gate.blockers,
        template_id=CLASS_LEVEL_ACTIVATION_TEMPLATE_ID,
        cost=activated_ability_cost(spec.to_activated_ability()),
        effects=(
            {
                "op": "gain_class_level",
                "card": "$source.zone_object",
                "level": parsed.level,
            },
        ),
        handlers=(class_level_handler_descriptor(spec),),
        mechanics=(CLASS_MECHANIC_ID,),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
    )


def _level_scope_node(
    *,
    face_id: str,
    oracle_id: str,
    parsed: ParsedClassLevel,
    row: tuple[str, str, SourceSpan],
    child_semantic_keys: tuple[str, ...],
    keywords: tuple[str, ...],
    capability_registry: Any,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode:
    line, _material, span = row
    gate = explicit_capabilities_gate(
        (CLASS_LIFECYCLE_CAPABILITY_ID,),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason="Class level scope depends on untrusted lifecycle semantics",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    provisional = OracleNode(
        node_id=f"{face_id}:class-level:{parsed.level}:scope",
        kind="static_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="characteristics.evaluate",
        lowerable=True,
        exact=not gate.blockers,
        template_id=CLASS_LEVEL_SCOPE_TEMPLATE_ID,
        mechanics=(CLASS_MECHANIC_ID,),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
    )
    parent_ability_id = generated_ability_id(
        kind=provisional.kind,
        face_id=face_id,
        line=provisional.span.line,
        static_declaration=True,
        node_id=provisional.node_id,
    )
    assert parent_ability_id is not None
    parent_key = f"{oracle_id}:{parent_ability_id}"
    return replace(
        provisional,
        handlers=(
            _scope_descriptor(
                parent_semantic_key=parent_key,
                child_semantic_keys=child_semantic_keys,
                keywords=keywords,
                minimum_level=parsed.level,
            ),
        ),
    )


def apply_class_context(
    *,
    context: ParsedClassContext | None,
    oracle_id: str,
    face_id: str,
    material_rows: Sequence[tuple[str, str, SourceSpan]],
    printed_keywords: Sequence[str],
    nodes: Sequence[OracleNode],
    residuals: list[OracleResidual],
    capability_registry: Any,
    capability_profile: str,
) -> tuple[OracleNode, ...]:
    """Attach level membership and materialize one ordinary Class lifecycle."""

    if context is None:
        return tuple(nodes)
    annotated: list[OracleNode] = []
    child_keys: list[list[str]] = [[], []]
    child_keywords: list[list[str]] = [[], []]
    for node in nodes:
        level_index = next(
            (
                index
                for index, level in enumerate(context.levels)
                if node.span.line in level.member_lines
            ),
            None,
        )
        if level_index is not None and _uses_cycle_sensitive_fragment(node):
            annotated.append(_unsupported_dynamic_child(node, residuals))
            continue
        if level_index is None or not node.exact:
            annotated.append(node)
            continue
        semantic_key = _semantic_key(
            oracle_id=oracle_id,
            face_id=face_id,
            node=node,
        )
        if semantic_key is None:
            annotated.append(node)
            continue
        gated = _with_class_dependency(
            replace(
                node,
                runtime_coverage=tuple(
                    dict.fromkeys(
                        (*node.runtime_coverage, CURRENT_ABILITY_FRAGMENT_COVERAGE)
                    )
                ),
            ),
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            residuals=residuals,
        )
        annotated.append(gated)
        if gated.exact:
            child_keys[level_index].append(semantic_key)
            child_keywords[level_index].extend(
                _keyword_members(node, printed_keywords=printed_keywords)
            )
    structural: list[OracleNode] = [
        _reminder_node(
            face_id=face_id,
            row=material_rows[context.reminder_row],
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            residuals=residuals,
        )
    ]
    for index, level in enumerate(context.levels):
        structural.append(
            _level_node(
                face_id=face_id,
                parsed=level,
                row=material_rows[level.activation_row],
                capability_registry=capability_registry,
                capability_profile=capability_profile,
                residuals=residuals,
            )
        )
        structural.append(
            _level_scope_node(
                face_id=face_id,
                oracle_id=oracle_id,
                parsed=level,
                row=material_rows[level.activation_row],
                child_semantic_keys=tuple(sorted(set(child_keys[index]))),
                keywords=tuple(
                    sorted(set(child_keywords[index]), key=str.casefold)
                ),
                capability_registry=capability_registry,
                capability_profile=capability_profile,
                residuals=residuals,
            )
        )
    return tuple(
        sorted(
            (*annotated, *structural),
            key=lambda node: (node.span.line, node.node_id),
        )
    )


__all__ = [
    "CLASS_LEVEL_ACTIVATION_TEMPLATE_ID",
    "CLASS_LEVEL_SCOPE_TEMPLATE_ID",
    "CLASS_REMINDER_TEMPLATE_ID",
    "ParsedClassContext",
    "ParsedClassLevel",
    "apply_class_context",
    "class_compilation_rows",
    "parse_class_context",
]
