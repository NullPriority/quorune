from __future__ import annotations

"""Closed grammatical subject and single-target reference binding."""

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from .closed_effect_programs import (
    CLOSED_EFFECT_PROGRAM_MECHANIC,
    ClosedEffectProgramTemplate,
    ComponentCompiler,
    _NESTED_SEQUENCE_MECHANICS,
    _UNSAFE_LINKAGE,
    _candidate_partitions,
    _partition,
)


BOUND_EFFECT_PROGRAM_MECHANIC = "bound-effect-program"
BOUND_EFFECT_PROGRAM_CAPABILITY = "resolution.effect_program.bound_references"
BOUND_EFFECT_PROGRAM_TEMPLATE_ID = "bound-effect-program-v1"
_SUBJECT = r"target player|target opponent|each player|each opponent|you"
_REFERENCE = r"it|that player|that creature|that permanent"
_TARGET_WORD = re.compile(r"\btarget\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class BoundEffectProgramTemplate(ClosedEffectProgramTemplate):
    @property
    def template_id(self) -> str:
        return BOUND_EFFECT_PROGRAM_TEMPLATE_ID


def _public_single_target(schema: Mapping[str, Any] | None) -> bool:
    if schema is None or type(schema.get("count")) is not int or schema.get("count") != 1:
        return False
    return (
        schema.get("zones") == ["player"] and schema.get("categories") == ["player"]
    ) or (
        schema.get("zones") == ["battlefield"] and schema.get("categories") == ["permanent"]
    )


def _program(compiled, *, schema) -> BoundEffectProgramTemplate | None:
    if not 2 <= len(compiled) <= 4 or any(
        template is None or not effects or not mechanics
        or BOUND_EFFECT_PROGRAM_MECHANIC in mechanics
        or CLOSED_EFFECT_PROGRAM_MECHANIC in mechanics
        for template, effects, _schema, mechanics in compiled
    ):
        return None
    effects = tuple(deepcopy(effect) for _, components, _, _ in compiled for effect in components)
    if not 2 <= len(effects) <= 8:
        return None
    return BoundEffectProgramTemplate(
        component_template_ids=tuple(str(component[0]) for component in compiled),
        component_effect_counts=tuple(len(component[1]) for component in compiled),
        _effects=effects, _target_schema=schema,
        mechanic_ids=tuple(dict.fromkeys((
            CLOSED_EFFECT_PROGRAM_MECHANIC, BOUND_EFFECT_PROGRAM_MECHANIC,
            *(mechanic for _, _, _, mechanics in compiled for mechanic in mechanics
              if mechanic not in _NESTED_SEQUENCE_MECHANICS),
        ))),
    )


def _shared_player_subject(text: str, compile_component: ComponentCompiler):
    match = re.fullmatch(rf"(?P<subject>{_SUBJECT}) (?P<body>.+)", text, re.IGNORECASE)
    if match is None:
        return None
    subject = match.group("subject")
    body = match.group("body")
    comma_clauses = _partition(body, ", ")
    partitions = (*_candidate_partitions(body), *((comma_clauses,) if comma_clauses else ()))
    for clauses in partitions:
        compiled = []
        for index, clause in enumerate(clauses):
            clause = re.sub(r"^(?:Then|And) ", "", clause, flags=re.IGNORECASE)
            if index and re.match(r"^That player\b", clause, re.IGNORECASE):
                if not subject.casefold().startswith("target "):
                    break
                clause = re.sub(r"^That player\b", subject, clause, flags=re.IGNORECASE)
            elif not re.match(rf"^(?:{_SUBJECT})\b", clause, re.IGNORECASE):
                clause = f"{subject} {clause}"
            # A second explicit target is a different target occurrence, not
            # grammatical ellipsis. The outer production rejects it first.
            compiled.append(compile_component(clause))
        if len(compiled) != len(clauses):
            continue
        targeted = tuple(component[2] for component in compiled if component[2] is not None)
        if targeted and (
            not _public_single_target(targeted[0])
            or targeted[0].get("categories") != ["player"]
            or any(schema != targeted[0] for schema in targeted[1:])
        ):
            continue
        result = _program(compiled, schema=targeted[0] if targeted else None)
        if result is not None:
            return result
    return None


def _single_target_reference(text: str, compile_component: ComponentCompiler):
    for clauses in _candidate_partitions(text):
        first = compile_component(clauses[0])
        schema = first[2]
        if first[0] is None or not _public_single_target(schema):
            continue
        assert schema is not None
        generic_subject = "target player" if schema["categories"] == ["player"] else "target permanent"
        compiled = [first]
        bound_reference_seen = False
        for clause in clauses[1:]:
            clause = re.sub(r"^Then ", "", clause, flags=re.IGNORECASE)
            original = compile_component(clause)
            if original[0] is not None:
                if original[2] is not None:
                    break
                compiled.append(original)
                continue
            # Only one object reference can be elided per clause. Possessive,
            # controller/owner, result, and newly selected references are not
            # inferred or bound by this production.
            references = tuple(re.finditer(rf"\b(?:{_REFERENCE})\b", clause, re.IGNORECASE))
            if len(references) != 1 or re.search(r"\b(?:its|their|that much|this way)\b", clause, re.IGNORECASE):
                break
            reference = references[0]
            noun = reference.group().casefold()
            if (
                (noun == "that player" and schema["categories"] != ["player"])
                or (noun in {"it", "that creature", "that permanent"}
                    and schema["categories"] != ["permanent"])
                or (noun == "that creature" and schema.get("types_any") != ["creature"])
            ):
                break
            normalized = clause[:reference.start()] + generic_subject + clause[reference.end():]
            component = compile_component(normalized)
            if (
                component[0] is None or component[2] is None
                or not _public_single_target(component[2])
                or component[2].get("categories") != schema.get("categories")
            ):
                break
            compiled.append(component)
            bound_reference_seen = True
        if len(compiled) != len(clauses) or not bound_reference_seen:
            continue
        result = _program(compiled, schema=schema)
        if result is not None:
            return result
    return None


def bound_effect_program_template(
    text: str, *, compile_component: ComponentCompiler
) -> BoundEffectProgramTemplate | None:
    """Lower one grammatical subject or one explicitly established target."""

    normalized = " ".join(text.strip().split())
    linkage_text = re.sub(r"\banother target\b", "target", normalized, flags=re.IGNORECASE)
    if not normalized or _UNSAFE_LINKAGE.search(linkage_text) or any(c in normalized for c in ('"', "(", ")")):
        return None
    target_count = len(_TARGET_WORD.findall(normalized))
    if target_count > 1:
        return None
    shared = _shared_player_subject(normalized, compile_component)
    if shared is not None:
        return shared
    return _single_target_reference(normalized, compile_component) if target_count == 1 else None


__all__ = [
    "BOUND_EFFECT_PROGRAM_CAPABILITY", "BOUND_EFFECT_PROGRAM_MECHANIC",
    "BOUND_EFFECT_PROGRAM_TEMPLATE_ID", "BoundEffectProgramTemplate", "bound_effect_program_template",
]
