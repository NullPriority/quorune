from __future__ import annotations

"""Resolution-time ordinary keyword choices for one permanent incarnation."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..keyword_counters import keyword_counter_mechanic
from ..rules.source_references import SourceReferenceSpec
from .fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate

MECHANIC = "temporary-source-keyword-choice"
CAPABILITY = "continuous.resolution.source_keyword_choice"


@dataclass(frozen=True, slots=True)
class SourceKeywordChoiceTemplate:
    branches: tuple[FixedSourceCharacteristicsTemplate, ...]
    prefix: FixedSourceCharacteristicsTemplate | None = None

    def compiled(self):
        words = tuple(branch.keywords[0] for branch in self.branches)
        mechanics = tuple(dict.fromkeys((
            MECHANIC, "cr-611-continuous-effects",
            *(value for branch in self.branches for value in branch.mechanics),
            *(keyword_counter_mechanic(word) or word.casefold() for word in words),
        )))
        effects: tuple[Mapping[str, Any], ...] = (
            *(self.prefix.effects if self.prefix else ()),
            {"op": "choose_option", "player": "$controller",
             "prompt": "Choose a temporary keyword.",
             "options": [{"id": word, "label": word} for word in words],
             "then_by_choice": {word: list(branch.effects)
                                for word, branch in zip(words, self.branches)}},
        )
        return "temporary-source-keyword-choice-v1", effects, None, mechanics


def source_keyword_choice_template(
    text: str, *, source_is_permanent: bool | None,
    source_card_types: tuple[str, ...], source_name: str | None,
) -> SourceKeywordChoiceTemplate | None:
    if source_is_permanent is not True:
        return None
    normalized = " ".join(text.strip().split())
    # Prefix and postfix duration forms describe the same complete instruction.
    if normalized.casefold().startswith("until end of turn, "):
        normalized = normalized[len("until end of turn, "):].rstrip(".") + " until end of turn."
    subject = rf"(?:this creature|{SourceReferenceSpec(source_name).regex_pattern})" if source_name else r"this creature"
    match = re.fullmatch(
        rf"(?P<subject>{subject}) (?:gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) and )?"
        r"gains your choice of (?P<choices>.+?) until end of turn\.?", normalized, re.I)
    if match is None:
        return None
    # This grammar intentionally owns creature-local fixed ordinary keywords.
    if "creature" not in {value.casefold() for value in source_card_types}:
        return None
    raw = match["choices"]
    alternatives = re.split(r"\s+or\s+", raw, flags=re.I)
    if len(alternatives) != 2:
        return None
    head, last = alternatives
    parts = [*head.removesuffix(",").split(","), last]
    branches = []
    try:
        for part in parts:
            word = " ".join(part.split()).title()
            branches.append(FixedSourceCharacteristicsTemplate(source_kind="creature", keywords=(word,)))
    except ValueError:
        return None
    words = [branch.keywords[0] for branch in branches]
    if len(set(words)) != len(words):
        return None
    power, toughness = int(match["power"] or 0), int(match["toughness"] or 0)
    prefix = FixedSourceCharacteristicsTemplate(source_kind="creature", power=power, toughness=toughness) if power or toughness else None
    return SourceKeywordChoiceTemplate(tuple(branches), prefix)
