from __future__ import annotations

"""Fixed Endure for an explicitly bound ability source."""

import re

from ..rules.endure_model import ENDURE_MECHANIC, ENDURE_OPERATION
from ..rules.source_references import SourceReferenceSpec
from .fixed_effect_clause_sequences import fixed_effect_clause_sequence_template
from .fixed_numbers import FIXED_COUNT_PATTERN


def fixed_endure_effect_template(text, *, source_name, source_is_permanent, allow_source_pronoun=False):
    if source_is_permanent is not True:
        return None
    match = re.fullmatch(r"(?P<subject>.+?) endures (?P<amount>[0-9]+)\.?", text.strip(), re.I)
    if match is None:
        return None
    subject = match["subject"]
    if (
        not re.fullmatch(r"this (?:creature|permanent|artifact|land|enchantment|planeswalker|battle)", subject, re.I)
        and not (allow_source_pronoun and subject.casefold() == "it")
        and not SourceReferenceSpec(source_name).matches(subject)
    ):
        return None
    return (
        "fixed-source-endure-v1",
        ({"op": ENDURE_OPERATION, "schema_version": 1, "amount": int(match["amount"])},),
        None,
        (ENDURE_MECHANIC,),
    )


def source_endure_trigger_result(text, *, source_name, allow_source_pronoun, effect_template):
    if "endures" not in text.casefold():
        return None

    def endure(clause):
        return fixed_endure_effect_template(
            clause, source_name=source_name, source_is_permanent=True,
            allow_source_pronoun=allow_source_pronoun,
        )

    direct = endure(text)
    if direct is not None:
        return direct
    if effect_template is None:
        return None
    loss = re.fullmatch(rf"(you lose (?:{FIXED_COUNT_PATTERN}) life) and (.+)", text.strip(), re.I)
    if loss is None or endure(loss[2]) is None:
        return None
    sequence = fixed_effect_clause_sequence_template(
        f"{loss[1]}. {loss[2]}",
        compile_clause=lambda clause: endure(clause) or effect_template(clause, card_name=source_name),
    )
    return sequence.compiled() if sequence is not None else None


def binding_endure_trigger_result(binding, text, *, source_name, effect_template):
    if binding.active_zone != "battlefield":
        return None
    source_pronoun = binding.event.value.endswith(".self") or binding.variant in {
        "source_attacks", "this_creature_blocks", "this_creature_becomes_blocked",
        "source_dealt_damage", "source_received_combat_damage", "source_enters_or_attacks",
    }
    return source_endure_trigger_result(
        text, source_name=source_name, allow_source_pronoun=source_pronoun, effect_template=effect_template,
    )
