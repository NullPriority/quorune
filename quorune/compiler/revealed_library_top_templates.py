"""Lower fixed results that use one shared public library reveal."""

from __future__ import annotations

import re

from ..replacement.immutable import FrozenMap
from ..rules.revealed_library_top import REVEAL_TOP_MECHANIC, RevealedLibraryTopSpec
from .oracle_source_text import without_ability_word_label


_HEADER = re.compile(
    r"Each player reveals the top card of (?:their|his or her) library\. "
    r"For each (?P<kind>land|nonland) card revealed this way, (?P<body>.+)\. "
    r"Then each player draws a card\.",
    re.IGNORECASE,
)
_LAND_THEN_NONLAND = re.compile(
    r"(?P<token>.+)\. Then (?P<pump>creatures you control get \+1/\+1 until end of turn) "
    r"for each nonland card revealed this way",
    re.IGNORECASE,
)
_MANA_AND_LIFE = re.compile(r"(?P<mana>add \{[WUBRGC]\}) and you gain 1 life", re.IGNORECASE)


def reveal_top_result_template(text, *, compile_literal):
    material = without_ability_word_label(" ".join(text.strip().split()))
    match = _HEADER.fullmatch(material) if material is not None else None
    if match is None:
        return None
    kind, body = match["kind"].casefold(), match["body"]
    land_then_nonland = _LAND_THEN_NONLAND.fullmatch(body) if kind == "land" else None
    mana_and_life = _MANA_AND_LIFE.fullmatch(body) if kind == "nonland" else None
    if land_then_nonland is not None:
        clauses = (("land", land_then_nonland["token"]), ("nonland", land_then_nonland["pump"]))
    elif mana_and_life is not None:
        clauses = (("nonland", mana_and_life["mana"]), ("nonland", "You gain 1 life"))
    else:
        clauses = ((kind, body),)

    followups = []
    for scope, clause in clauses:
        literal = re.sub(r"^you create ", "Create ", clause, flags=re.IGNORECASE)
        template_id, effects, targets, _ = compile_literal(literal + ".")
        if template_id is None or len(effects) != 1 or targets is not None:
            return None
        followups.append((scope, FrozenMap(effects[0])))
    try:
        spec = RevealedLibraryTopSpec(tuple(followups))
    except (TypeError, ValueError):
        return None
    return "public-library-top-result-v1", (spec.effect(),), None, (REVEAL_TOP_MECHANIC,)
