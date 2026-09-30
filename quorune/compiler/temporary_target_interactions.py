from __future__ import annotations

"""Closed temporary effects over one direct creature target."""

from copy import deepcopy
from dataclasses import dataclass, replace
import re
from typing import Any, Mapping

from ..carddb import CardRecord

from ..ability_fragments import (
    ability_fragment_to_dict,
    parse_protection_line,
)
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..rules.temporary_target_interactions import (
    TEMPORARY_TARGET_INTERACTION_CAPABILITY,
    TargetConditionSpec,
)
from ..rules.temporary_declaration_restrictions import (
    temporary_declaration_restriction,
)
from .fixed_target_effect_sequences import (
    fixed_target_characteristics_effect_template,
)
from .ir_model import OracleNode, OracleResidual, append_residual


TEMPORARY_TARGET_INTERACTION_MECHANIC = "temporary-target-interaction"
_TRAILING_REMINDER = re.compile(
    r"\s*\([^()]*(?:\([^()]*\)[^()]*)*\)\s*$"
)
_COLORS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}


def _without_reminder(text: str) -> str:
    return _TRAILING_REMINDER.sub("", " ".join(text.strip().split())).strip()


def _protection_fragment(quality: str) -> Mapping[str, Any] | None:
    parsed = parse_protection_line(f"Protection from {quality}")
    if parsed is None or len(parsed) != 1:
        return None
    return ability_fragment_to_dict(parsed[0])


def _target_schema(relation: str) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "types_any": ["creature"],
        "count": 1,
    }
    if relation != "any":
        schema["controller_relation"] = relation
    return schema


def _target_relation(subject: str) -> str | None:
    normalized = " ".join(subject.casefold().split())
    return {
        "target creature": "any",
        "target creature you control": "you",
    }.get(normalized)


@dataclass(frozen=True, slots=True)
class TemporaryTargetInteractionTemplate:
    _effects: tuple[Mapping[str, Any], ...]
    _target_schema: Mapping[str, Any]
    _mechanics: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self._effects or not self._mechanics:
            raise ValueError("Temporary target interaction is empty")
        object.__setattr__(self, "_effects", deepcopy(self._effects))
        object.__setattr__(self, "_target_schema", deepcopy(self._target_schema))

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any],
        tuple[str, ...],
    ]:
        return (
            "temporary-target-interaction-v1",
            deepcopy(self._effects),
            deepcopy(self._target_schema),
            self._mechanics,
        )


def _fixed_protection_template(
    text: str,
) -> TemporaryTargetInteractionTemplate | None:
    match = re.fullmatch(
        r"(?P<subject>target creature(?: you control)?) gains protection "
        r"from (?P<quality>white|blue|black|red|green|artifacts) until "
        r"end of turn\.?",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return None
    relation = _target_relation(match.group("subject"))
    fragment = _protection_fragment(match.group("quality"))
    if relation is None or fragment is None:
        return None
    return TemporaryTargetInteractionTemplate(
        _effects=(
            {
                "op": "grant_keyword_until_end_of_turn",
                "card": "$target.0",
                "keyword": "Protection",
                "ability_fragment": fragment,
            },
        ),
        _target_schema=_target_schema(relation),
        _mechanics=(
            TEMPORARY_TARGET_INTERACTION_MECHANIC,
            "cr-115-targets",
            "cr-611-continuous-effects",
            "protection",
        ),
    )


def _chosen_protection_template(
    text: str,
) -> TemporaryTargetInteractionTemplate | None:
    match = re.fullmatch(
        r"(?P<subject>target creature(?: you control)?) gains protection "
        r"from the color of (?P<chooser>your|its controller's) choice "
        r"until end of turn\.?",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return None
    relation = _target_relation(match.group("subject"))
    if relation is None:
        return None
    fragments = {
        symbol: _protection_fragment(word)
        for word, symbol in _COLORS.items()
    }
    if any(value is None for value in fragments.values()):
        return None
    return TemporaryTargetInteractionTemplate(
        _effects=(
            {
                "op": "choose_option",
                "player": (
                    "$controller"
                    if match.group("chooser").casefold() == "your"
                    else "$target.current_controller.0"
                ),
                "prompt": "Choose a protection color.",
                "options": [
                    {"id": symbol, "label": word.title()}
                    for word, symbol in _COLORS.items()
                ],
                "then_by_choice": {
                    symbol: [
                        {
                            "op": "grant_keyword_until_end_of_turn",
                            "card": "$target.0",
                            "keyword": "Protection",
                            "ability_fragment": fragment,
                        }
                    ]
                    for symbol, fragment in fragments.items()
                },
            },
        ),
        _target_schema=_target_schema(relation),
        _mechanics=(
            TEMPORARY_TARGET_INTERACTION_MECHANIC,
            "cr-115-targets",
            "cr-611-continuous-effects",
            "protection",
        ),
    )


def _chosen_keyword_template(
    text: str,
) -> TemporaryTargetInteractionTemplate | None:
    match = re.fullmatch(
        r"(?P<subject>target creature(?: you control)?) "
        r"(?:(?P<stats>gets [+-]\d+/[+-]\d+) and )?gains your choice of "
        r"(?P<first>[a-z ]+) or (?P<second>[a-z ]+) until end of turn\.?",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return None
    relation = _target_relation(match.group("subject"))
    if relation is None:
        return None
    branches = {}
    keywords = []
    mechanics = []
    for word in (match.group("first"), match.group("second")):
        parsed = fixed_target_characteristics_effect_template(
            f"It gains {word} until end of turn.", existing_target=True
        )
        if parsed is None or len(parsed.keywords) != 1:
            return None
        keyword = parsed.keywords[0]
        if keyword in keywords:
            return None
        keywords.append(keyword)
        branches[keyword] = list(parsed.effects)
        mechanics.extend(parsed.compiled()[3])
    stats = match.group("stats")
    base = (
        fixed_target_characteristics_effect_template(
            f"{match.group('subject')} {stats} until end of turn."
        ) if stats else None
    )
    return TemporaryTargetInteractionTemplate(
        _effects=(
            *(base.effects if base is not None else ()),
            {
                "op": "choose_option",
                "player": "$controller",
                "prompt": "Choose a temporary keyword.",
                "options": [{"id": word, "label": word} for word in keywords],
                "then_by_choice": branches,
            },
        ),
        _target_schema=_target_schema(relation),
        _mechanics=tuple(dict.fromkeys((
            TEMPORARY_TARGET_INTERACTION_MECHANIC,
            "cr-115-targets", "cr-611-continuous-effects", *mechanics,
        ))),
    )


def _condition(value: str) -> TargetConditionSpec | None:
    normalized = " ".join(value.casefold().split())
    if normalized in {"it's legendary", "it is legendary"}:
        return TargetConditionSpec(
            queries_any=(
                ObjectQuerySpec(
                    zones=("battlefield",),
                    supertypes_all=("legendary",),
                ),
            )
        )
    if normalized == "it's an artifact creature":
        return TargetConditionSpec(
            queries_any=(
                ObjectQuerySpec(
                    zones=("battlefield",),
                    types_all=("artifact", "creature"),
                ),
            )
        )
    if normalized == "it has a counter on it":
        return TargetConditionSpec(has_any_counter=True)
    if normalized in {"it's a goblin or orc", "it's a vampire", "it's a spirit"}:
        subtypes = {
            "it's a goblin or orc": ("goblin", "orc"),
            "it's a vampire": ("vampire",),
            "it's a spirit": ("spirit",),
        }[normalized]
        return TargetConditionSpec(
            queries_any=(
                ObjectQuerySpec(
                    zones=("battlefield",),
                    subtypes_any=subtypes,
                ),
            )
        )
    if normalized == "it's an enchanted creature or enchantment creature":
        return TargetConditionSpec(
            queries_any=(
                ObjectQuerySpec(
                    zones=("battlefield",),
                    state_predicate=PermanentStatePredicateSpec(enchanted=True),
                ),
                ObjectQuerySpec(
                    zones=("battlefield",),
                    types_all=("creature", "enchantment"),
                ),
            )
        )
    return None


def _with_condition(
    effect: Mapping[str, Any], condition: TargetConditionSpec
) -> Mapping[str, Any]:
    return {**dict(effect), "target_condition": condition.to_dict()}


def _conditional_template(
    text: str,
) -> TemporaryTargetInteractionTemplate | None:
    match = re.fullmatch(
        r"(?P<base>Target creature(?: you control)? gets [+-]\d+/[+-]\d+ "
        r"until end of turn\.) If (?P<condition>.+?), (?:it|that creature) "
        r"also gains (?P<keywords>.+?) until end of turn\.?",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return None
    base = fixed_target_characteristics_effect_template(match.group("base"))
    conditional = fixed_target_characteristics_effect_template(
        f"It gains {match.group('keywords')} until end of turn.",
        existing_target=True,
    )
    condition = _condition(match.group("condition"))
    if base is None or conditional is None or condition is None:
        return None
    _template, base_effects, schema, mechanics = base.compiled()
    _conditional_template_id, conditional_effects, _schema, conditional_mechanics = (
        conditional.compiled()
    )
    assert schema is not None
    return TemporaryTargetInteractionTemplate(
        _effects=tuple(base_effects) + tuple(
            _with_condition(effect, condition) for effect in conditional_effects
        ),
        _target_schema=schema,
        _mechanics=tuple(
            dict.fromkeys(
                (
                    TEMPORARY_TARGET_INTERACTION_MECHANIC,
                    *mechanics,
                    *conditional_mechanics,
                )
            )
        ),
    )


def _expanded_characteristic_template(
    text: str,
) -> TemporaryTargetInteractionTemplate | None:
    # Keep the original leaf boundary intact. The new production owns cost-X,
    # the supported comma-list spelling and represented combat keyword grants.
    if fixed_target_characteristics_effect_template(text) is not None:
        return None
    parsed = fixed_target_characteristics_effect_template(text, extended=True)
    if parsed is None or parsed.target_schema is None:
        return None
    _template, effects, schema, mechanics = parsed.compiled()
    assert schema is not None
    return TemporaryTargetInteractionTemplate(
        _effects=effects,
        _target_schema=schema,
        _mechanics=(TEMPORARY_TARGET_INTERACTION_MECHANIC, *mechanics),
    )


def _declaration_or_regeneration_template(
    text: str,
) -> TemporaryTargetInteractionTemplate | None:
    patterns = (
        (
            re.compile(
                r"(?P<base>(?:Another )?Target creature(?: .+?)? (?:gets .+?|gains .+?) "
                r"until end of turn) and can't be blocked this turn\.?",
                re.IGNORECASE,
            ),
            "unblockable",
            None,
        ),
        (
            re.compile(
                r"(?P<base>Target creature(?: you control)? (?:gets .+?|gains .+?) "
                r"until end of turn)\. Regenerate it\.?",
                re.IGNORECASE,
            ),
            None,
            "regenerate",
        ),
    )
    for pattern, restriction, operation in patterns:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        base = fixed_target_characteristics_effect_template(
            match.group("base") + "."
        )
        if base is None:
            return None
        _template, effects, schema, mechanics = base.compiled()
        assert schema is not None
        tail = (
            {
                "op": "grant_declaration_restriction_until_end_of_turn",
                "card": "$target.0",
                "restriction": restriction,
            }
            if restriction is not None
            else {"op": str(operation), "card": "$target.0"}
        )
        return TemporaryTargetInteractionTemplate(
            _effects=(*effects, tail),
            _target_schema=schema,
            _mechanics=tuple(
                dict.fromkeys(
                    (
                        TEMPORARY_TARGET_INTERACTION_MECHANIC,
                        *mechanics,
                        *(("regenerate",) if operation else ()),
                        *(temporary_declaration_restriction(restriction).mechanics
                          if restriction else ()),
                    )
                )
            ),
        )
    return None


def temporary_target_interaction_effect_template(
    text: str,
) -> TemporaryTargetInteractionTemplate | None:
    """Lower one closed temporary interaction over a direct creature target."""

    normalized = _without_reminder(text)
    for parser in (
        _fixed_protection_template,
        _chosen_protection_template,
        _chosen_keyword_template,
        _conditional_template,
        _declaration_or_regeneration_template,
        _expanded_characteristic_template,
    ):
        template = parser(normalized)
        if template is not None:
            return template
    return None


def _uses_cost_x(value: object) -> bool:
    if isinstance(value, str):
        return value in {"$x", "$neg_x"}
    if isinstance(value, Mapping):
        return any(_uses_cost_x(child) for child in value.values())
    return isinstance(value, (list, tuple)) and any(_uses_cost_x(child) for child in value)


def restrict_temporary_interaction_context(
    record: CardRecord,
    face_id: str,
    nodes: list[OracleNode],
    residuals: list[OracleResidual],
) -> list[OracleNode]:
    """Cost-X belongs only to a spell face whose mana cost actually binds X."""

    mana_cost = record.mana_cost
    if record.faces:
        mana_cost = next(
            (str(face.get("mana_cost") or "") for face in record.faces
             if str(face.get("name") or "") == face_id),
            "",
        )
    result = []
    for node in nodes:
        if TEMPORARY_TARGET_INTERACTION_MECHANIC in node.mechanics and _uses_cost_x(node.effects) and (
            node.kind != "spell_ability" or "{X}" not in mana_cost.upper()
        ):
            residual = append_residual(
                residuals, kind="quantity_expression", text=node.text,
                span=node.span, reason="Temporary cost-X requires a spell mana cost that binds X",
                blockers=("unbound or unsupported temporary X quantity",),
            )
            node = replace(node, lowerable=False, exact=False,
                           residual_ids=(*node.residual_ids, residual))
        result.append(node)
    return result


__all__ = [
    "TEMPORARY_TARGET_INTERACTION_CAPABILITY",
    "TEMPORARY_TARGET_INTERACTION_MECHANIC",
    "TemporaryTargetInteractionTemplate",
    "restrict_temporary_interaction_context",
    "temporary_target_interaction_effect_template",
]
