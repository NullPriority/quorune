from __future__ import annotations

"""Closed printed-order sequences of independently typed effect clauses."""

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping


FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC = "fixed-effect-clause-sequence"

CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
ClauseCompiler = Callable[[str], CompiledEffectTemplate]


def _period_sentence_clauses(text: str) -> tuple[str, str] | None:
    """Split exactly two top-level sentences without entering quoted rules."""

    normalized = text.strip()
    if not normalized.endswith("."):
        return None
    clauses: list[str] = []
    start = 0
    parenthetical_depth = 0
    quoted = False
    for index, character in enumerate(normalized):
        if character == '"':
            quoted = not quoted
            continue
        if quoted:
            continue
        if character == "(":
            parenthetical_depth += 1
            continue
        if character == ")":
            if parenthetical_depth == 0:
                return None
            parenthetical_depth -= 1
            continue
        if (
            character == "."
            and parenthetical_depth == 0
            and (
                index == len(normalized) - 1
                or normalized[index + 1].isspace()
            )
        ):
            clause = normalized[start : index + 1].strip()
            if not clause:
                return None
            clauses.append(clause)
            start = index + 1
    if quoted or parenthetical_depth or normalized[start:].strip():
        return None
    if len(clauses) != 2:
        return None
    return clauses[0], clauses[1]


def _top_level_scope_text(text: str) -> str:
    """Exclude quoted and parenthetical rules from outer subject checks."""
    result=[];quoted=False;depth=0
    for character in text:
        if character == '"':quoted=not quoted;result.append(' ');continue
        if not quoted and character == '(':depth+=1;result.append(' ');continue
        if not quoted and character == ')' and depth:depth-=1;result.append(' ');continue
        result.append(' ' if quoted or depth else character)
    return ''.join(result)


def _two_sentence_clauses(text: str) -> tuple[str, str] | None:
    """Retain period grammar and close two top-level printed-order clauses."""
    existing = _period_sentence_clauses(text)
    if 'then' not in text.casefold() or (existing is not None and not re.match(r'Then\b',existing[1],re.I)):
        return existing
    normalized = text.strip()
    if not normalized.endswith('.'):
        return None
    clauses=[];start=0;depth=0;quoted=False;ordered=False;comma_then=False;skip_to=0
    for index,character in enumerate(normalized):
        if index < skip_to:continue
        if character == '"':quoted=not quoted;continue
        if quoted:continue
        if character == '(':depth+=1;continue
        if character == ')':
            if depth == 0:return None
            depth-=1;continue
        if depth:continue
        separator = re.match(r',\s+then\s+',normalized[index:],re.I) if character == ',' else None
        if separator is not None:
            clause=normalized[start:index].strip()
            if not clause:return None
            clauses.append(clause+'.');start=index+separator.end();skip_to=start;ordered=True;comma_then=True
        elif character == '.' and (index == len(normalized)-1 or normalized[index+1].isspace()):
            clause=normalized[start:index+1].strip()
            if clause == '.':return None
            clauses.append(clause);start=index+1
    if quoted or depth or normalized[start:].strip() or len(clauses) != 2:
        return None
    second=clauses[1]
    if re.match(r'Then\s+',second,re.I):
        second=re.sub(r'^Then\s+','',second,flags=re.I);ordered=True
    if not ordered or not second:
        return None
    first_scope=_top_level_scope_text(clauses[0]);second_scope=_top_level_scope_text(second)
    if re.search(r'\b(?:it|its|that|those|them|their|they|this way|if|unless|otherwise|when you do)\b',second_scope,re.I):
        return None
    if re.search(r'\bsearch(?:es)?\b',first_scope,re.I) or re.search(r'\bsearch(?:es)?\b',second_scope,re.I):
        return None
    if comma_then and re.search(r'\bmay\b',first_scope,re.I):
        return None
    if re.match(r'(?:draws|discards|mills|gains|loses|creates|sacrifices|exiles|shuffles|returns)\b',second,re.I):
        actor=re.match(r'(?P<actor>(?:Each|Target)\s+(?:player|opponent))\s+',first_scope,re.I)
        if actor is None:return None
        second=' '.join(actor['actor'].split())+' '+second
    return clauses[0],second


@dataclass(frozen=True, slots=True)
class FixedEffectClauseSequenceTemplate:
    """Exactly two independently closed effects sharing at most one target."""

    component_template_ids: tuple[str, str]
    _effects: tuple[Mapping[str, Any], Mapping[str, Any]]
    _target_schema: Mapping[str, Any] | None
    mechanic_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            len(self.component_template_ids) != 2
            or any(not value for value in self.component_template_ids)
            or len(self._effects) != 2
            or len(self.mechanic_ids) < 2
            or self.mechanic_ids[0] != FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC
        ):
            raise ValueError("Fixed effect-clause sequence is malformed")
        object.__setattr__(self, "_effects", deepcopy(self._effects))
        object.__setattr__(self, "_target_schema", deepcopy(self._target_schema))

    @property
    def template_id(self) -> str:
        return "fixed-effect-clause-sequence-v1"

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


def fixed_effect_clause_sequence_template(
    text: str,
    *,
    compile_clause: ClauseCompiler,
) -> FixedEffectClauseSequenceTemplate | None:
    """Lower two complete clauses already owned independently, in order."""

    clauses = _two_sentence_clauses(text)
    if clauses is None:
        return None
    compiled = tuple(compile_clause(clause) for clause in clauses)
    if any(
        template_id is None
        or len(effects) != 1
        or not mechanics
        or FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC in mechanics
        for template_id, effects, _target_schema, mechanics in compiled
    ):
        return None
    targeted = tuple(
        target_schema
        for _template_id, _effects, target_schema, _mechanics in compiled
        if target_schema is not None
    )
    if len(targeted) > 1:
        return None
    return FixedEffectClauseSequenceTemplate(
        component_template_ids=tuple(
            str(template_id)
            for template_id, _effects, _target_schema, _mechanics in compiled
        ),
        _effects=tuple(
            deepcopy(effects[0])
            for _template_id, effects, _target_schema, _mechanics in compiled
        ),
        _target_schema=targeted[0] if targeted else None,
        mechanic_ids=tuple(
            dict.fromkeys(
                (
                    FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC,
                    *(
                        mechanic
                        for _template_id, _effects, _target_schema, mechanics
                        in compiled
                        for mechanic in mechanics
                    ),
                )
            )
        ),
    )


__all__ = [
    "FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC",
    "FixedEffectClauseSequenceTemplate",
    "fixed_effect_clause_sequence_template",
]
