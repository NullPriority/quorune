"""Pair the three printed loyalty instructions on one source face."""

from . import regex_working_set as re
from ..rules.linked_loyalty_exile_model import linked_loyalty_exile_instruction
from ..rules.source_references import SourceReferenceSpec
from ..rules.targeted_opponent_library_top import opponent_top_exile_target_schema


_TOP = re.compile(
    r"\+2: Exile the top three cards of target opponent['’]s library\.",
    re.IGNORECASE,
)
_OPPONENT_ZONES = re.compile(
    r"[-\u2212]10: Exile all cards from all opponents['’] hands and graveyards\.",
    re.IGNORECASE,
)


def linked_loyalty_activation_compiler(fallback, *, material_rows, card_name, card_types):
    """Bind the linked exiles only when the whole printed ability set matches."""
    if "planeswalker" not in card_types or len(material_rows) != 3:
        return fallback
    (_, top, top_span), (_, creature, creature_span), (_, opponents, opponents_span) = material_rows
    if _TOP.fullmatch(top) is None or _OPPONENT_ZONES.fullmatch(opponents) is None:
        return fallback
    source = SourceReferenceSpec(card_name).regex_pattern
    if re.fullmatch(
        rf"[-\u2212]X: Put a creature card with mana value X exiled with {source} "
        r"onto the battlefield under your control\. That creature is a Nightmare in addition to its other types\.",
        creature, flags=re.IGNORECASE,
    ) is None:
        return fallback
    compiled = {
        top_span.line: (
            "linked-loyalty-target-top-v1", (linked_loyalty_exile_instruction("target_top"),),
            opponent_top_exile_target_schema(), ("cr-115-targets", "exile"),
        ),
        creature_span.line: (
            "linked-loyalty-creature-entry-v1", (linked_loyalty_exile_instruction("choose"),),
            None, ("exile",),
        ),
        opponents_span.line: (
            "linked-loyalty-opponent-zones-v1", (linked_loyalty_exile_instruction("opponent_zones"),),
            None, ("exile",),
        ),
    }

    def compile_activation(**values):
        result = compiled.get(values["span"].line)
        if result is not None:
            values["effect_template"] = lambda _body, **_source: result
        return fallback(**values)

    return compile_activation
