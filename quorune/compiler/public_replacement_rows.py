from __future__ import annotations

"""Retain raw spans while joining closed public-state spell clauses."""

from ..delayed_triggers import fixed_next_upkeep_payment_cost
from . import regex_working_set as re
from .ir_model import SourceSpan
from .oracle_source_text import without_ability_word_label
from .public_self_replacement_templates import normalize_public_replacement_text
from .tap_state_templates import COMBAT_PREVENTION_ATTACKERS_UNTAP_TEMPLATE
from .fixed_control_templates import TWO_CONTROLLER_CREATURE_TRANSFER_TEMPLATE


def public_replacement_material_rows(rows, *, oracle_text, source_name, compile_effect):
    if len(rows) == 2 and all(row[1].casefold().startswith("choose an opponent.") for row in rows):
        from .paired_token_creator_templates import PAIRED_TOKEN_TEMPLATE

        first, second = rows
        combined = first[1] + " " + second[1]
        if compile_effect(combined, card_name=source_name)[0] == PAIRED_TOKEN_TEMPLATE:
            span = SourceSpan(first[2].start, second[2].end, first[2].line)
            return [(oracle_text[span.start:span.end], combined, span)]
    result=[]
    for row in rows:
        material = row[1]
        if result and material.casefold().endswith(", exile that creature."):
            from .resolution_condition_templates import CONTROLLED_ARTIFACT_TAP_EXILE_TEMPLATE

            previous = result[-1]
            combined = previous[1] + " " + material
            if compile_effect(combined, card_name=source_name)[0] == CONTROLLED_ARTIFACT_TAP_EXILE_TEMPLATE:
                span = SourceSpan(previous[2].start, row[2].end, previous[2].line)
                result[-1] = (oracle_text[span.start:span.end], combined, span)
                continue
        if result and material.casefold().startswith("ferocious — that creature gains indestructible"):
            from .resolution_condition_templates import BLOCKER_PROTECTION_TEMPLATE

            previous = result[-1]
            combined = previous[1] + " " + material
            if compile_effect(combined, card_name=source_name)[0] == BLOCKER_PROTECTION_TEMPLATE:
                span = SourceSpan(previous[2].start, row[2].end, previous[2].line)
                result[-1] = (oracle_text[span.start:span.end], combined, span)
                continue

        if result and material.casefold().startswith("ferocious — that creature also gains "):
            from ..rules.ferocious_keyword_append import FEROCIOUS_KEYWORD_TEMPLATE

            previous = result[-1]
            combined = previous[1] + " " + material
            if compile_effect(combined, card_name=source_name)[0] == FEROCIOUS_KEYWORD_TEMPLATE:
                span = SourceSpan(previous[2].start, row[2].end, previous[2].line)
                result[-1] = (oracle_text[span.start:span.end], combined, span)
                continue
        if result and material.casefold().startswith("exile ") and result[-1][1].casefold().endswith(
            ("phase out.", "no maximum hand size for the rest of the game."),
        ):
            from ..rules.announced_x_draw_replacement import TEMPLATE as ANNOUNCED_X_DRAW_REPLACEMENT_TEMPLATE
            from ..rules.next_personal_turn_protection import NEXT_PERSONAL_TURN_PROTECTION_TEMPLATE
            from ..rules.spell_self_exile import SELF_EXILE
            from ..rules.targeted_opponent_protection import TEMPLATE as TARGET_OPPONENT_TEMPLATE

            previous = result[-1]
            combined = previous[1] + " " + material
            templates = {f"{SELF_EXILE.template}+{body}" for body in
                         (NEXT_PERSONAL_TURN_PROTECTION_TEMPLATE, ANNOUNCED_X_DRAW_REPLACEMENT_TEMPLATE, TARGET_OPPONENT_TEMPLATE)}
            if compile_effect(combined, card_name=source_name)[0] in templates:
                span = SourceSpan(previous[2].start, row[2].end, previous[2].line)
                result[-1] = (oracle_text[span.start:span.end], combined, span)
                continue
        if result and material.casefold().startswith(("until your next turn, your opponents can't cast", "exile ")):
            from ..rules.airbend import TEMPLATE as AIRBEND_TEMPLATE
            from ..rules.spell_self_exile import SELF_EXILE

            previous = result[-1]
            if "airbend all other creatures" in previous[1].casefold():
                combined = previous[1] + " " + material
                if compile_effect(combined, card_name=source_name)[0] in {AIRBEND_TEMPLATE, f"{SELF_EXILE.template}+{AIRBEND_TEMPLATE}"}:
                    span = SourceSpan(previous[2].start, row[2].end, previous[2].line)
                    result[-1] = (oracle_text[span.start:span.end], combined, span)
                    continue
        if result and material.casefold().replace("’", "'") == "you draw a card at the beginning of the next turn's upkeep.":
            from .stack_counter_followup import STACK_NEXT_TURN_DRAW_PAIR_TEMPLATE

            previous = result[-1]
            combined = previous[1] + " " + material
            if compile_effect(combined, card_name=source_name)[0] == STACK_NEXT_TURN_DRAW_PAIR_TEMPLATE:
                span = SourceSpan(previous[2].start, row[2].end, previous[2].line)
                result[-1] = (oracle_text[span.start:span.end], combined, span)
                continue
        if result and material.casefold().startswith("at the beginning of your next upkeep, pay "):
            previous = result[-1]
            combined = previous[1] + " " + material
            compiled = compile_effect(combined, card_name=source_name)
            if (compiled[0] == "fixed-effect-clause-sequence-v1"
                    and len(compiled[1]) == 2 and fixed_next_upkeep_payment_cost(compiled[1][1]) is not None):
                span = SourceSpan(previous[2].start, row[2].end, previous[2].line)
                result[-1] = (oracle_text[span.start:span.end], combined, span)
                continue
        if len(result) >= 2 and material.casefold() == "those creatures gain haste until end of turn.":
            initial, transfer = result[-2:]
            combined = " ".join((initial[1], transfer[1], material))
            if compile_effect(combined, card_name=source_name)[0] == TWO_CONTROLLER_CREATURE_TRANSFER_TEMPLATE:
                span = SourceSpan(initial[2].start, row[2].end, initial[2].line)
                result[-2:] = [(oracle_text[span.start:span.end], combined, span)]
                continue
        tail = without_ability_word_label(material) if "Then if " in material else None
        ordered = tail is not None and tail.startswith("Then if ")
        attacking_untap = material.casefold().startswith("each attacking creature ")
        if not result or (not ordered and not attacking_untap and not re.search(r'\binstead\b', material, re.I)):
            result.append(row)
            continue
        previous = result[-1]
        if ordered:
            combined = previous[1] + " " + tail
            templates = {"fixed-resolution-public-condition-v1"}
        elif attacking_untap:
            combined = previous[1] + " " + material
            templates = {COMBAT_PREVENTION_ATTACKERS_UNTAP_TEMPLATE}
        else:
            combined = normalize_public_replacement_text(previous[1] + " " + material)
            templates = {
                "fixed-public-self-replacement-v1", "public-same-target-self-replacement-v1",
                "public-source-self-replacement-v1",
                "current-target-self-replacement-v1",
            }
        compiled = compile_effect(combined, card_name=source_name)
        if compiled[0] not in templates:
            result.append(row)
            continue
        span=SourceSpan(previous[2].start,row[2].end,previous[2].line)
        result[-1]=(oracle_text[span.start:span.end],combined,span)
    return result
