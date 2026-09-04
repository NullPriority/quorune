from __future__ import annotations

"""Compile CR 711 Leveler striations around already-typed child abilities."""

from dataclasses import dataclass, replace
import re
from typing import Any, Iterable, Mapping, Sequence

from ..ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE,
    STATIC_COMPONENT_SCOPE_FRAGMENT_HANDLER_ID,
    StaticComponentScopeSpec,
    ability_fragment_to_dict,
)
from ..counter_keyword_abilities import compile_fixed_counter_keyword_ability
from ..leveler_bands import (
    LEVELER_BANDS_CAPABILITY_ID,
    LEVELER_MECHANIC_ID,
    LevelerBandSpec,
    LevelerBandsSpec,
    leveler_bands_handler_descriptor,
)
from .dependency_gate import explicit_capabilities_gate
from .generated_program_identity import generated_ability_id
from .ir_model import (
    OracleFaceIR,
    OracleNode,
    OracleResidual,
    SourceSpan,
    append_residual,
)
from .oracle_source_text import material_source_lines


_LEVEL_RANGE = re.compile(
    r"^LEVEL\s+(?P<minimum>[1-9]\d*)"
    r"(?:(?P<open>\+)|[\-\u2013\u2014](?P<maximum>[1-9]\d*))$",
    re.IGNORECASE,
)
_POWER_TOUGHNESS = re.compile(
    r"^(?P<power>\d+)/(?P<toughness>\d+)$"
)
_CYCLE_SENSITIVE_FRAGMENT_KINDS = frozenset(
    {
        "dynamic_power_toughness",
        "query_characteristic_modifier",
        "query_power_toughness_definition",
    }
)


@dataclass(frozen=True, slots=True)
class ParsedLevelerBand:
    minimum_level: int
    maximum_level: int | None
    power: int
    toughness: int
    header_row: int
    power_toughness_row: int
    member_lines: frozenset[int]


@dataclass(frozen=True, slots=True)
class ParsedLevelerContext:
    bands: tuple[ParsedLevelerBand, ParsedLevelerBand]

    @property
    def consumed_rows(self) -> frozenset[int]:
        return frozenset(
            row
            for band in self.bands
            for row in (band.header_row, band.power_toughness_row)
        )


def leveler_compilation_rows(
    context: ParsedLevelerContext | None,
    rows: Sequence[tuple[str, str, SourceSpan]],
) -> Iterable[tuple[int, tuple[str, str, SourceSpan]]]:
    """Preserve source numbering while withholding represented band rows."""

    consumed = context.consumed_rows if context is not None else frozenset()
    return (
        (index, row)
        for index, row in enumerate(rows, 1)
        if index - 1 not in consumed
    )


def parse_leveler_context(
    *,
    layout: str,
    type_line: str,
    material_rows: Sequence[tuple[str, str, SourceSpan]],
) -> ParsedLevelerContext | None:
    """Recognize exactly two canonical Leveler text-box striations."""

    if layout != "leveler" or "creature" not in type_line.casefold():
        return None
    if not material_rows or compile_fixed_counter_keyword_ability(
        material_line=material_rows[0][1],
        oracle_line=material_rows[0][0],
        line_index=0,
        mechanic="level up",
        printed_power=None,
    ) is None:
        return None
    headers = tuple(
        (index, _LEVEL_RANGE.fullmatch(material_line))
        for index, (_line, material_line, _span) in enumerate(material_rows)
        if _LEVEL_RANGE.fullmatch(material_line) is not None
    )
    if len(headers) != 2 or headers[0][0] != 1:
        return None
    parsed: list[ParsedLevelerBand] = []
    for position, (header_row, match) in enumerate(headers):
        assert match is not None
        power_toughness_row = header_row + 1
        if power_toughness_row >= len(material_rows):
            return None
        power_toughness = _POWER_TOUGHNESS.fullmatch(
            material_rows[power_toughness_row][1]
        )
        if power_toughness is None:
            return None
        end = (
            headers[position + 1][0]
            if position + 1 < len(headers)
            else len(material_rows)
        )
        if any(
            _LEVEL_RANGE.fullmatch(material_rows[index][1]) is not None
            for index in range(power_toughness_row + 1, end)
        ):
            return None
        parsed.append(
            ParsedLevelerBand(
                minimum_level=int(match.group("minimum")),
                maximum_level=(
                    None
                    if match.group("open")
                    else int(match.group("maximum"))
                ),
                power=int(power_toughness.group("power")),
                toughness=int(power_toughness.group("toughness")),
                header_row=header_row,
                power_toughness_row=power_toughness_row,
                member_lines=frozenset(
                    material_rows[index][2].line
                    for index in range(power_toughness_row + 1, end)
                ),
            )
        )
    lower, upper = parsed
    if (
        lower.maximum_level is None
        or upper.maximum_level is not None
        or upper.minimum_level != lower.maximum_level + 1
    ):
        return None
    return ParsedLevelerContext((lower, upper))


def leveler_source_context(
    layout: str,
    type_line: str,
    oracle_text: str,
    *,
    ordinary_saga: bool,
) -> tuple[
    tuple[tuple[str, str, SourceSpan], ...],
    ParsedLevelerContext | None,
]:
    """Build material rows and their optional Leveler grouping once."""

    rows = tuple(
        material_source_lines(oracle_text, ordinary_saga=ordinary_saga)
    )
    return rows, parse_leveler_context(
        layout=layout,
        type_line=type_line,
        material_rows=rows,
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
    *,
    oracle_id: str,
    face_id: str,
    node: OracleNode,
) -> str | None:
    ability_id = generated_ability_id(
        kind=node.kind,
        face_id=face_id,
        line=node.span.line,
        static_declaration=_is_static_declaration(node),
        node_id=node.node_id,
    )
    return f"{oracle_id}:{ability_id}" if ability_id is not None else None


def _with_leveler_dependency(
    node: OracleNode,
    *,
    capability_registry: Any,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode:
    gate = explicit_capabilities_gate(
        (*node.capability_dependencies, LEVELER_BANDS_CAPABILITY_ID),
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
                reason="Level-band ability depends on untrusted range semantics",
                blockers=gate.blockers,
            ),
        )
    return replace(
        node,
        exact=node.exact and not gate.blockers,
        mechanics=tuple(dict.fromkeys((*node.mechanics, LEVELER_MECHANIC_ID))),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def _keyword_members(
    node: OracleNode,
    *,
    printed_keywords: Sequence[str],
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
                and mechanic.casefold() != "level up"
            },
            key=str.casefold,
        )
    )


def _uses_cycle_sensitive_fragment(node: OracleNode) -> bool:
    return any(
        isinstance(fragment, Mapping)
        and fragment.get("kind") in _CYCLE_SENSITIVE_FRAGMENT_KINDS
        for descriptor in node.handlers
        for fragment in (descriptor.get("fragment"),)
    )


def _unsupported_dynamic_child(
    node: OracleNode,
    residuals: list[OracleResidual],
) -> OracleNode:
    return replace(
        node,
        exact=False,
        handlers=(),
        runtime_coverage=(),
        mechanics=tuple(
            dict.fromkeys((*node.mechanics, LEVELER_MECHANIC_ID))
        ),
        residual_ids=(
            *node.residual_ids,
            append_residual(
                residuals,
                kind="unsupported_leveler_dynamic_child",
                text=node.text,
                span=node.span,
                reason=(
                    "Level-band dynamic characteristics require a "
                    "cycle-safe applicability boundary"
                ),
                blockers=(
                    "cycle-safe level-band dynamic characteristic",
                ),
            ),
        ),
    )


def _component_scope_descriptor(
    *,
    parent_semantic_key: str,
    child_semantic_keys: tuple[str, ...],
    keywords: tuple[str, ...],
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
            )
        ),
    }


def _band_node(
    *,
    face_id: str,
    band_index: int,
    parsed: ParsedLevelerBand,
    material_rows: Sequence[tuple[str, str, SourceSpan]],
    oracle_text: str,
    handlers: Sequence[Mapping[str, Any]],
    capability_registry: Any,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode:
    header = material_rows[parsed.header_row][2]
    power_toughness = material_rows[parsed.power_toughness_row][2]
    span = SourceSpan(header.start, power_toughness.end, header.line)
    gate = explicit_capabilities_gate(
        (LEVELER_BANDS_CAPABILITY_ID,),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=oracle_text[span.start : span.end],
                span=span,
                reason="Leveler band depends on untrusted characteristic semantics",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return OracleNode(
        node_id=f"{face_id}:level-band:{band_index}",
        kind="static_ability",
        text=oracle_text[span.start : span.end],
        span=span,
        active_zone="battlefield",
        event=("characteristics.evaluate" if handlers else "continuous"),
        lowerable=True,
        exact=not gate.blockers,
        template_id=(
            "leveler-bands-v1" if handlers else "leveler-band-range-v1"
        ),
        handlers=tuple(handlers),
        mechanics=(LEVELER_MECHANIC_ID,),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def apply_leveler_context(
    *,
    context: ParsedLevelerContext | None,
    oracle_id: str,
    face_id: str,
    oracle_text: str,
    material_rows: Sequence[tuple[str, str, SourceSpan]],
    printed_keywords: Sequence[str],
    nodes: Sequence[OracleNode],
    residuals: list[OracleResidual],
    capability_registry: Any,
    capability_profile: str,
) -> tuple[OracleNode, ...]:
    """Attach range membership and materialize the two level symbols."""

    if context is None:
        return tuple(nodes)
    annotated: list[OracleNode] = []
    band_keys: list[list[str]] = [[], []]
    band_keywords: list[list[str]] = [[], []]
    scoped_keywords = [
        keyword
        for keyword in printed_keywords
        if keyword.casefold() != "level up"
    ]
    for node in nodes:
        band_index = next(
            (
                index
                for index, band in enumerate(context.bands)
                if node.span.line in band.member_lines
            ),
            None,
        )
        if band_index is not None and _uses_cycle_sensitive_fragment(node):
            annotated.append(_unsupported_dynamic_child(node, residuals))
            continue
        if band_index is None or not node.exact:
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
        gated = _with_leveler_dependency(
            replace(
                node,
                runtime_coverage=tuple(
                    dict.fromkeys(
                        (
                            *node.runtime_coverage,
                            CURRENT_ABILITY_FRAGMENT_COVERAGE,
                        )
                    )
                ),
            ),
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            residuals=residuals,
        )
        annotated.append(gated)
        if gated.exact:
            band_keys[band_index].append(semantic_key)
            band_keywords[band_index].extend(
                _keyword_members(node, printed_keywords=printed_keywords)
            )
    bands = tuple(
        LevelerBandSpec(
            minimum_level=parsed.minimum_level,
            maximum_level=parsed.maximum_level,
            power=parsed.power,
            toughness=parsed.toughness,
            keywords=tuple(sorted(set(band_keywords[index]), key=str.casefold)),
            semantic_keys=tuple(sorted(set(band_keys[index]), key=str.casefold)),
        )
        for index, parsed in enumerate(context.bands)
    )
    spec = LevelerBandsSpec(bands)
    outer = _band_node(
        face_id=face_id,
        band_index=1,
        parsed=context.bands[0],
        material_rows=material_rows,
        oracle_text=oracle_text,
        handlers=(leveler_bands_handler_descriptor(spec),),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )
    child_keys = tuple(sorted(key for values in band_keys for key in values))
    outer_key = _semantic_key(
        oracle_id=oracle_id,
        face_id=face_id,
        node=outer,
    )
    if child_keys or scoped_keywords:
        assert outer_key is not None
        outer = replace(
            outer,
            handlers=(
                *outer.handlers,
                _component_scope_descriptor(
                    parent_semantic_key=outer_key,
                    child_semantic_keys=child_keys,
                    keywords=tuple(
                        sorted(set(scoped_keywords), key=str.casefold)
                    ),
                ),
            ),
        )
    upper = _band_node(
        face_id=face_id,
        band_index=2,
        parsed=context.bands[1],
        material_rows=material_rows,
        oracle_text=oracle_text,
        handlers=(),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )
    band_nodes = (outer, upper)
    return tuple(
        sorted(
            (*annotated, *band_nodes),
            key=lambda node: (node.span.line, node.node_id),
        )
    )


def leveler_face_ir(
    context: ParsedLevelerContext | None,
    oracle_id: str,
    face_id: str,
    face_name: str,
    oracle_text: str,
    material_rows: Sequence[tuple[str, str, SourceSpan]],
    keywords: Sequence[str],
    nodes: Sequence[OracleNode],
    residuals: list[OracleResidual],
    capability_registry: Any,
    capability_profile: str,
) -> OracleFaceIR:
    """Finish one face after contextual Leveler compilation."""

    resolved_nodes = apply_leveler_context(
        context=context,
        oracle_id=oracle_id,
        face_id=face_id,
        oracle_text=oracle_text,
        material_rows=material_rows,
        printed_keywords=keywords,
        nodes=nodes,
        residuals=residuals,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    return OracleFaceIR(
        face_id=face_id,
        face_name=face_name,
        oracle_text=oracle_text,
        nodes=resolved_nodes,
        residuals=tuple(residuals),
    )


__all__ = [
    "ParsedLevelerBand",
    "ParsedLevelerContext",
    "apply_leveler_context",
    "leveler_compilation_rows",
    "leveler_face_ir",
    "leveler_source_context",
    "parse_leveler_context",
]
