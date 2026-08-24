from __future__ import annotations

"""Closed printed-order programs composed from existing typed effects."""

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping


CLOSED_EFFECT_PROGRAM_MECHANIC = "closed-effect-program"
CLOSED_EFFECT_PROGRAM_TEMPLATE_ID = "closed-effect-program-v1"

CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
ComponentCompiler = Callable[[str], CompiledEffectTemplate]

_NESTED_SEQUENCE_MECHANICS = frozenset(
    {
        "fixed-controller-effect-sequence",
        "fixed-counter-controller-effect-sequence",
        "fixed-effect-clause-sequence",
        "fixed-source-effect-sequence",
        "fixed-target-effect-sequence",
    }
)
_UNSAFE_LINKAGE = re.compile(
    r"\b(?:if|unless|may|random|repeat|for each|this way|that much|"
    r"equal to|another target|different target)\b",
    re.IGNORECASE,
)


def _top_level_positions(text: str, needle: str) -> tuple[int, ...] | None:
    positions: list[int] = []
    parenthetical_depth = 0
    quoted = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == '"':
            quoted = not quoted
            index += 1
            continue
        if not quoted:
            if character == "(":
                parenthetical_depth += 1
            elif character == ")":
                if parenthetical_depth == 0:
                    return None
                parenthetical_depth -= 1
            elif parenthetical_depth == 0 and text.startswith(needle, index):
                positions.append(index)
                index += len(needle)
                continue
        index += 1
    if quoted or parenthetical_depth:
        return None
    return tuple(positions)


def _with_period(text: str) -> str:
    normalized = text.strip()
    return normalized if normalized.endswith(".") else f"{normalized}."


def _partition(text: str, needle: str) -> tuple[str, ...] | None:
    positions = _top_level_positions(text, needle)
    if positions is None or not 1 <= len(positions) <= 3:
        return None
    clauses: list[str] = []
    start = 0
    for position in positions:
        clause = text[start:position].strip().rstrip(",")
        if not clause:
            return None
        clauses.append(_with_period(clause))
        start = position + len(needle)
    final = text[start:].strip()
    if not final:
        return None
    clauses.append(_with_period(final))
    return tuple(clauses)


def _sentence_partition(text: str) -> tuple[str, ...] | None:
    normalized = text.strip()
    positions = _top_level_positions(normalized, ". ")
    if positions is None or not 1 <= len(positions) <= 3:
        return None
    clauses: list[str] = []
    start = 0
    for position in positions:
        clauses.append(normalized[start : position + 1].strip())
        start = position + 2
    final = normalized[start:].strip()
    if not final:
        return None
    clauses.append(_with_period(final))
    return tuple(clauses)


def _candidate_partitions(text: str) -> tuple[tuple[str, ...], ...]:
    candidates: list[tuple[str, ...]] = []
    sentences = _sentence_partition(text)
    if sentences is not None:
        candidates.append(sentences)
    for separator in (", then ", " and "):
        clauses = _partition(text, separator)
        if clauses is not None:
            candidates.append(clauses)
    return tuple(candidates)


@dataclass(frozen=True, slots=True)
class ClosedEffectProgramTemplate:
    """Two to four mandatory typed components sharing at most one target."""

    component_template_ids: tuple[str, ...]
    component_effect_counts: tuple[int, ...]
    _effects: tuple[Mapping[str, Any], ...]
    _target_schema: Mapping[str, Any] | None
    mechanic_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not 2 <= len(self.component_template_ids) <= 4
            or len(self.component_effect_counts)
            != len(self.component_template_ids)
            or any(not value for value in self.component_template_ids)
            or any(value <= 0 for value in self.component_effect_counts)
            or sum(self.component_effect_counts) != len(self._effects)
            or not 2 <= len(self._effects) <= 8
            or not self.mechanic_ids
            or self.mechanic_ids[0] != CLOSED_EFFECT_PROGRAM_MECHANIC
        ):
            raise ValueError("Closed effect program is malformed")
        object.__setattr__(self, "_effects", deepcopy(self._effects))
        object.__setattr__(self, "_target_schema", deepcopy(self._target_schema))

    @property
    def template_id(self) -> str:
        return CLOSED_EFFECT_PROGRAM_TEMPLATE_ID

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return deepcopy(self._effects)

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        return deepcopy(self._target_schema)

    def compiled(self) -> CompiledEffectTemplate:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanic_ids,
        )


def closed_effect_program_template(
    text: str,
    *,
    compile_component: ComponentCompiler,
) -> ClosedEffectProgramTemplate | None:
    """Compose independently typed mandatory components in printed order."""

    normalized = text.strip()
    if not normalized or _UNSAFE_LINKAGE.search(normalized):
        return None
    for clauses in _candidate_partitions(normalized):
        compiled = tuple(compile_component(clause) for clause in clauses)
        if any(
            template_id is None
            or not effects
            or not mechanics
            or CLOSED_EFFECT_PROGRAM_MECHANIC in mechanics
            for template_id, effects, _target_schema, mechanics in compiled
        ):
            continue
        effects = tuple(
            deepcopy(effect)
            for _template_id, component_effects, _target_schema, _mechanics
            in compiled
            for effect in component_effects
        )
        if not 2 <= len(effects) <= 8:
            continue
        targeted = tuple(
            target_schema
            for _template_id, _effects, target_schema, _mechanics in compiled
            if target_schema is not None
        )
        if len(targeted) > 1 or (
            targeted and targeted[0].get("count") != 1
        ):
            continue
        return ClosedEffectProgramTemplate(
            component_template_ids=tuple(
                str(template_id)
                for template_id, _effects, _target_schema, _mechanics
                in compiled
            ),
            component_effect_counts=tuple(
                len(component_effects)
                for _template_id, component_effects, _target_schema, _mechanics
                in compiled
            ),
            _effects=effects,
            _target_schema=targeted[0] if targeted else None,
            mechanic_ids=tuple(
                dict.fromkeys(
                    (
                        CLOSED_EFFECT_PROGRAM_MECHANIC,
                        *(
                            mechanic
                            for _template_id, _effects, _target_schema, mechanics
                            in compiled
                            for mechanic in mechanics
                            if mechanic not in _NESTED_SEQUENCE_MECHANICS
                        ),
                    )
                )
            ),
        )
    return None


__all__ = [
    "CLOSED_EFFECT_PROGRAM_MECHANIC",
    "CLOSED_EFFECT_PROGRAM_TEMPLATE_ID",
    "ClosedEffectProgramTemplate",
    "closed_effect_program_template",
]
