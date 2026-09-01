from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..card_programs.admission import (
    CARD_PROGRAM_ADMISSION_FIELD,
    CompleteCardProgramAdmission,
    descriptor_requires_complete_card_program,
)
from ..carddb import CardRecord
from ..rules.capabilities import CapabilityRegistry
from ..semantics import SemanticProgram


COMPOSED_SPELL_EFFECT_SEQUENCE = "composed-spell-effect-sequence-v1"
_SPAN_START_FIELD = "start"


@dataclass(frozen=True, slots=True)
class _EffectComponentView:
    capability_dependencies: list[str]
    cost_schema: dict[str, Any] | None
    coverage: list[str]
    effects: list[dict[str, Any]]
    event_condition: dict[str, Any] | None
    provenance: dict[str, str]
    target_schema: dict[str, Any] | None


def _copy_mapping(value: Any) -> dict[str, Any] | None:
    return dict(value) if value is not None else None


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def generated_node_groups(
    face: Any,
    *,
    ability_id_for: Callable[[Any], str | None],
) -> tuple[tuple[Any, ...], ...]:
    """Group source nodes that describe one physical spell ability."""

    groups: list[list[Any]] = []
    spell_groups: dict[str, list[Any]] = {}
    for node in face.nodes:
        ability_id = ability_id_for(node)
        if node.kind == "spell_ability" and ability_id is not None:
            group = spell_groups.get(ability_id)
            if group is None:
                group = []
                spell_groups[ability_id] = group
                groups.append(group)
            group.append(node)
        else:
            groups.append([node])
    return tuple(tuple(group) for group in groups)


def is_closed_composed_spell_effect_program(
    program: SemanticProgram,
    *,
    component_recognizers: Sequence[Callable[[Any], bool]],
) -> bool:
    """Validate a printed-order sequence of independently closed clauses."""

    if str(program.provenance.get("template_id") or "") != (
        COMPOSED_SPELL_EFFECT_SEQUENCE
    ):
        return False
    components = program.provenance.get("components")
    if not isinstance(components, list) or len(components) < 2:
        return False
    expected_fields = {
        "capability_dependencies",
        "cost_schema",
        "coverage",
        "effect_count",
        "event_condition",
        "source_span",
        "target_schema",
        "template_id",
    }
    offset = 0
    dependencies: set[str] = set()
    cost_schemas: list[dict[str, Any]] = []
    event_conditions: list[dict[str, Any]] = []
    target_schemas: list[dict[str, Any]] = []
    previous_end = -1
    source_spans = program.provenance.get("source_spans")
    if (
        not isinstance(source_spans, list)
        or len(source_spans) != len(components)
    ):
        return False
    component_spans: list[dict[str, int]] = []
    for component in components:
        if (
            not isinstance(component, Mapping)
            or set(component) != expected_fields
        ):
            return False
        template_id = str(component.get("template_id") or "")
        effect_count = component.get("effect_count")
        source_span = component.get("source_span")
        coverage = component.get("coverage")
        component_dependencies = component.get("capability_dependencies")
        cost_schema = component.get("cost_schema")
        event_condition = component.get("event_condition")
        target_schema = component.get("target_schema")
        if (
            not template_id
            or template_id == COMPOSED_SPELL_EFFECT_SEQUENCE
            or type(effect_count) is not int
            or effect_count <= 0
            or not isinstance(source_span, Mapping)
            or set(source_span) != {"end", "line", _SPAN_START_FIELD}
            or any(type(source_span[field]) is not int for field in source_span)
            or source_span[_SPAN_START_FIELD] < previous_end
            or source_span["end"] <= source_span[_SPAN_START_FIELD]
            or source_span["line"] < 1
            or not isinstance(coverage, list)
            or any(not isinstance(value, str) or not value for value in coverage)
            or not isinstance(component_dependencies, list)
            or any(
                not isinstance(value, str) or not value
                for value in component_dependencies
            )
            or component_dependencies != sorted(set(component_dependencies))
            or (
                cost_schema is not None
                and not isinstance(cost_schema, Mapping)
            )
            or (
                event_condition is not None
                and not isinstance(event_condition, Mapping)
            )
            or (
                target_schema is not None
                and not isinstance(target_schema, Mapping)
            )
        ):
            return False
        previous_end = source_span["end"]
        component_spans.append(dict(source_span))
        end = offset + effect_count
        if end > len(program.effects):
            return False
        if cost_schema is not None:
            cost_schemas.append(dict(cost_schema))
        if event_condition is not None:
            event_conditions.append(dict(event_condition))
        if target_schema is not None:
            target_schemas.append(dict(target_schema))
        component_shape = _EffectComponentView(
            capability_dependencies=list(component_dependencies),
            cost_schema=(
                dict(cost_schema) if cost_schema is not None else None
            ),
            coverage=list(coverage),
            effects=[dict(effect) for effect in program.effects[offset:end]],
            event_condition=(
                dict(event_condition)
                if event_condition is not None
                else None
            ),
            provenance={"template_id": template_id},
            target_schema=(
                dict(target_schema) if target_schema is not None else None
            ),
        )
        if not any(
            recognizer(component_shape)
            for recognizer in component_recognizers
        ):
            return False
        dependencies.update(component_dependencies)
        offset = end
    expected_source_span = {
        _SPAN_START_FIELD: component_spans[0][_SPAN_START_FIELD],
        "end": component_spans[-1]["end"],
        "line": component_spans[0]["line"],
    }
    return bool(
        offset == len(program.effects)
        and source_spans == component_spans
        and program.provenance.get("source_span") == expected_source_span
        and len(cost_schemas) <= 1
        and (cost_schemas[0] if cost_schemas else None)
        == program.cost_schema
        and len(event_conditions) <= 1
        and (event_conditions[0] if event_conditions else None)
        == program.event_condition
        and len(target_schemas) <= 1
        and (target_schemas[0] if target_schemas else None)
        == program.target_schema
        and sorted(dependencies) == sorted(program.capability_dependencies)
    )


def _validated_group_identity(
    nodes: tuple[Any, ...],
    *,
    trust_level: str,
    ability_id_for: Callable[[Any], str | None],
    is_static_declaration: Callable[[Any], bool],
    is_independently_exact: Callable[[Any], bool],
) -> tuple[Any, bool, str] | None:
    if not nodes:
        return None
    first = nodes[0]
    composed_spell = len(nodes) > 1
    if composed_spell and any(node.kind != "spell_ability" for node in nodes):
        return None
    if trust_level == "trusted" and any(
        not is_independently_exact(node) for node in nodes
    ):
        return None
    def has_effect_payload(node: Any) -> bool:
        if node.effects:
            return True
        schema = node.target_schema
        modes = schema.get("modes") if isinstance(schema, Mapping) else None
        return bool(
            isinstance(modes, Mapping)
            and all(
                isinstance(definition, Mapping)
                and bool(definition.get("effects"))
                for definition in modes.values()
            )
        )

    if any(
        not node.lowerable
        or (
            not has_effect_payload(node)
            and not is_static_declaration(node)
            and not node.handlers
        )
        for node in nodes
    ):
        return None
    ability_id = ability_id_for(first)
    if ability_id is None or any(
        ability_id_for(node) != ability_id for node in nodes
    ):
        return None
    if composed_spell and (
        any(node.handlers for node in nodes)
        or len({node.active_zone for node in nodes}) != 1
        or len({node.event for node in nodes}) != 1
        or sum(node.target_schema is not None for node in nodes) > 1
        or sum(node.cost is not None for node in nodes) > 1
        or sum(node.event_condition is not None for node in nodes) > 1
    ):
        return None
    return first, composed_spell, ability_id


def _node_group_provenance(
    *,
    face: Any,
    nodes: tuple[Any, ...],
    ir: Any,
    rulings_hash: str,
    authored_by: str,
    trust_level: str,
    capability_closure: Any,
    composed_spell: bool,
) -> dict[str, Any]:
    source_spans = [asdict(node.span) for node in nodes]
    source_span = (
        {
            _SPAN_START_FIELD: source_spans[0][_SPAN_START_FIELD],
            "end": source_spans[-1]["end"],
            "line": source_spans[0]["line"],
        }
        if composed_spell
        else source_spans[0]
    )
    template_id = (
        COMPOSED_SPELL_EFFECT_SEQUENCE
        if composed_spell
        else nodes[0].template_id
    )
    components = [
        {
            "capability_dependencies": sorted(
                node.capability_dependencies
            ),
            "coverage": _ordered_unique(
                (*node.runtime_coverage, *node.mechanics)
            ),
            "cost_schema": _copy_mapping(node.cost),
            "effect_count": len(node.effects),
            "event_condition": _copy_mapping(node.event_condition),
            "source_span": asdict(node.span),
            "target_schema": _copy_mapping(node.target_schema),
            "template_id": node.template_id,
        }
        for node in nodes
    ]
    requires_complete_card_program = any(
        descriptor_requires_complete_card_program(handler)
        for node in nodes
        for handler in node.handlers
    )
    return {
        "source_oracle_hash": ir.oracle_hash,
        "source_rulings_hash": rulings_hash,
        "authored_by": authored_by,
        "review_status": (
            "capability_closure_verified"
            if trust_level == "trusted"
            and capability_closure is not None
            and capability_closure.trusted
            else (
                "legacy_dependency_verified"
                if trust_level == "trusted"
                else "generated_review_required"
            )
        ),
        "template_id": template_id,
        "face_id": face.face_id,
        "source_span": source_span,
        "semantic_hash": ir.semantic_hash,
        "dependency_trust": (
            "capability_closure_verified"
            if capability_closure is not None
            and capability_closure.trusted
            else (
                "pending_mechanic_contracts"
                if trust_level != "trusted"
                else "verified"
            )
        ),
        **(
            {
                CARD_PROGRAM_ADMISSION_FIELD: (
                    CompleteCardProgramAdmission.from_oracle_ir(ir).to_dict()
                )
            }
            if requires_complete_card_program
            else {}
        ),
        **(
            {"source_spans": source_spans, "components": components}
            if composed_spell
            else {}
        ),
        **(
            {
                "capability_registry_fingerprint": (
                    capability_closure.registry_fingerprint
                ),
                "capability_closure_fingerprint": (
                    capability_closure.fingerprint
                ),
                "capability_profile": capability_closure.profile,
            }
            if capability_closure is not None
            else {}
        ),
    }


def program_from_generated_node_group(
    *,
    record: CardRecord,
    face: Any,
    nodes: tuple[Any, ...],
    ir: Any,
    rulings_hash: str,
    authored_by: str,
    trust_level: str,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    ability_id_for: Callable[[Any], str | None],
    is_static_declaration: Callable[[Any], bool],
    is_independently_exact: Callable[[Any], bool],
    represented_mechanics_for: Callable[[Any], Iterable[str]],
    generated_coverage: Callable[..., str],
) -> SemanticProgram | None:
    """Lower one source-ordered node group without losing sibling clauses."""

    identity = _validated_group_identity(
        nodes,
        trust_level=trust_level,
        ability_id_for=ability_id_for,
        is_static_declaration=is_static_declaration,
        is_independently_exact=is_independently_exact,
    )
    if identity is None:
        return None
    first, composed_spell, ability_id = identity

    dependencies = (
        _ordered_unique(
            capability
            for node in nodes
            for capability in node.capability_dependencies
        )
        if composed_spell
        else list(first.capability_dependencies)
    )
    capability_closure = (
        capability_registry.closure(
            dependencies,
            profile=capability_profile,
        )
        if capability_registry is not None and dependencies
        else None
    )
    represented_mechanics = _ordered_unique(
        mechanic
        for node in nodes
        for mechanic in represented_mechanics_for(node)
    )
    runtime_handler_declaration = any(node.handlers for node in nodes)
    target_node = next(
        (node for node in nodes if node.target_schema is not None),
        None,
    )
    cost_node = next((node for node in nodes if node.cost is not None), None)
    condition_node = next(
        (node for node in nodes if node.event_condition is not None),
        None,
    )
    effects = [
        dict(effect)
        for node in nodes
        for effect in node.effects
    ]
    provenance = _node_group_provenance(
        face=face,
        nodes=nodes,
        ir=ir,
        rulings_hash=rulings_hash,
        authored_by=authored_by,
        trust_level=trust_level,
        capability_closure=capability_closure,
        composed_spell=composed_spell,
    )
    if str(first.kind).startswith("granted_"):
        provenance["granted_only"] = True
    return SemanticProgram(
        key=f"{record.oracle_id}:{ability_id}",
        label=(
            record.name
            if first.kind == "spell_ability"
            else f"{record.name} — {first.text}"
        ),
        effects=effects,
        handlers=[dict(handler) for node in nodes for handler in node.handlers],
        destination="graveyard" if first.kind == "spell_ability" else None,
        requires_arbiter=trust_level != "trusted",
        version=1,
        oracle_id=record.oracle_id,
        ability_id=ability_id,
        active_zone=first.active_zone,
        event=first.event,
        trust_level=trust_level,
        provenance=provenance,
        tests=[f"oracle_template:{node.template_id}" for node in nodes],
        target_schema=(
            _copy_mapping(target_node.target_schema)
            if target_node is not None
            else None
        ),
        cost_schema=(
            _copy_mapping(cost_node.cost) if cost_node is not None else None
        ),
        event_condition=(
            _copy_mapping(condition_node.event_condition)
            if condition_node is not None
            else None
        ),
        coverage=_ordered_unique(
            (
                "generated_oracle_ir",
                generated_coverage(
                    kind=first.kind,
                    runtime_handler=runtime_handler_declaration,
                ),
                *(
                    coverage
                    for node in nodes
                    for coverage in node.runtime_coverage
                ),
                *represented_mechanics,
            )
        ),
        capability_dependencies=dependencies,
        capability_closure=(
            capability_closure.to_dict()
            if capability_closure is not None
            else None
        ),
    )


__all__ = [
    "COMPOSED_SPELL_EFFECT_SEQUENCE",
    "generated_node_groups",
    "is_closed_composed_spell_effect_program",
    "program_from_generated_node_group",
]
