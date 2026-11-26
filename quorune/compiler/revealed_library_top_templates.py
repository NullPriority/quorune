"""Lower fixed results that use one shared public library reveal."""

from __future__ import annotations

from . import regex_working_set as re

from ..replacement.immutable import FrozenMap
from ..rules.revealed_library_top import REVEAL_TOP_MECHANIC, RevealedLibraryTopSpec
from ..rules.revealed_creature_statistics import REVEALED_CREATURE_MECHANIC, revealed_creature_effect
from .oracle_source_text import without_ability_word_label
from .scry_templates import fixed_scry_effect_template


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


def one_revealed_card_placement_template(text):
    from ..rules.one_revealed_card_placement import OneRevealedCardPlacement

    matched = re.fullmatch(
        r"Reveal the top card of your library\. "
        r"If it['’]s a (?P<kind>creature|permanent) card, (?P<optional>you may )?put it onto the battlefield\. "
        r"Otherwise, (?P<fallback>you may put (?:it|that card) on the bottom of your library|put it into your graveyard)\.",
        " ".join(text.strip().split()), re.IGNORECASE,
    )
    if matched is None:
        return None
    fallback = "optional_bottom" if matched["fallback"].casefold().startswith("you may") else "graveyard"
    try:
        spec = OneRevealedCardPlacement(matched["kind"].casefold(), bool(matched["optional"]), fallback)
    except ValueError:
        return None
    return "one-revealed-card-placement-v1", (spec.effect(),), None, ("fixed-library-selection",)

def scry_revealed_creature_template(text):
    material = without_ability_word_label(" ".join(text.strip().split()))
    if material is None:
        return None
    match = re.fullmatch(
        r"(?P<scry>Scry [^,.]+), then reveal the top card of your library\. "
        r"If it['’]s a creature card, you draw cards equal to its power "
        r"and you gain life equal to its toughness\.",
        material, re.IGNORECASE,
    )
    if match is None:
        return None
    scry = fixed_scry_effect_template(match["scry"])
    if scry is None:
        return None
    _, effects, _, mechanics = scry.compiled()
    return ("scry-revealed-creature-results-v1", (*effects, revealed_creature_effect()),
            None, (*mechanics, REVEALED_CREATURE_MECHANIC))


def public_top_reveal_template(text, *, folded, compile_literal):
    """Dispatch complete public library-top reveal bodies to their shape owners."""
    if "you draw cards equal to its power" in folded:
        return scry_revealed_creature_template(text)
    if folded.startswith("reveal the top card of your library.") and "otherwise," in folded:
        return one_revealed_card_placement_template(text)
    if "card revealed this way" in folded and "each player reveals" in folded:
        return reveal_top_result_template(text, compile_literal=compile_literal)
    return None
