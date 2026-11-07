"""Bind a closed literal continuation to actual destruction results."""
from __future__ import annotations

import re

from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
from ..rules.destruction_result_model import DESTRUCTION_RESULT_MECHANIC, DestructionFollowup
from ..object_predicate import ObjectQuerySpec
from .closed_effect_programs import CLOSED_EFFECT_PROGRAM_MECHANIC

_SUBJECT = r"(?P<subject>(?:nontoken )?(?:permanent|creature|enchantment)(?: you controlled that was)?)"
_TERMINAL_COUNT = re.compile(
    r"(?P<effect>(?:Then )?(?:(?:You )?draw a card|create a [^.]+? token|"
    r"You (?:gain|lose) [12] life|put a \+1/\+1 counter on this creature))"
    rf" for each {_SUBJECT} destroyed this way\.$", re.I,
)
_CONTROLLED_LIFE = re.compile(
    r"You gain life equal to the number of (?P<subject>creatures you controlled that were) destroyed this way\.$", re.I,
)
_PREFIXED_TOKEN = re.compile(
    rf"For each {_SUBJECT} destroyed this way, you (?P<effect>create a [^.]+? token)\.$", re.I,
)


def _literal_followup(text):
    if match := _TERMINAL_COUNT.search(text):
        return match, match["effect"] + "."
    if match := _CONTROLLED_LIFE.search(text):
        return match, "You gain 1 life."
    if match := _PREFIXED_TOKEN.search(text):
        body = match["effect"]
        return match, body[0].upper() + body[1:] + "."
    return None


def destruction_followup_template(text, *, compile_literal):
    if not text.lstrip().casefold().startswith("destroy ") or "destroyed this way" not in text.casefold():
        return None
    normalized = text.strip()
    literal = _literal_followup(normalized)
    if literal is None:
        return None
    match, replacement = literal
    _, effects, target_schema, mechanics = compile_literal(normalized[:match.start()] + replacement)
    if len(effects) != 2 or effects[0].get("op") != "destroy_all":
        return None
    subject = match["subject"].casefold()
    if subject == "creatures you controlled that were":
        subject = "creature you controlled that was"
    card_type = subject.removeprefix("nontoken ").removesuffix(" you controlled that was")
    controller = PermanentControllerRelation.ACTOR if "you controlled" in subject else PermanentControllerRelation.ANY
    try:
        followup = DestructionFollowup(
            AffectedPermanentSetSpec(
                ObjectQuerySpec(
                    zones=("battlefield",), types_all=() if card_type == "permanent" else (card_type,),
                    token=False if subject.startswith("nontoken ") else None,
                ),
                controller_relation=controller,
            ),
            effects[1],
        )
    except (TypeError, ValueError):
        return None
    markers = tuple(
        mechanic for mechanic in mechanics
        if mechanic not in {"fixed-effect-clause-sequence", CLOSED_EFFECT_PROGRAM_MECHANIC, "bound-effect-program"}
    )
    return (
        "destroy-fixed-set-result-v1",
        ({**effects[0], "followup": followup.to_dict()},),
        target_schema,
        (*markers, DESTRUCTION_RESULT_MECHANIC),
    )
