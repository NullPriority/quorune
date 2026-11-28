from __future__ import annotations

"""Fixed source-entry counter clauses over actual colored mana units paid."""

from . import regex_working_set as re

from ..entry_counter_model import DynamicEntryCounterAmountSpec, DynamicEntryCounterCalculation, DynamicEntryCounterValueSource
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .counter_placement_templates import FIXED_COUNTER_NAME_PATTERN
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


_PAID_GREEN_PAIRS = re.compile(
    r"(?P<subject>.+?) enters with three \+1/\+1 counters on it for each \{G\}\{G\} spent to cast it\.?",
    re.I,
)


def paid_color_entry_amount(text: str, *, source_name: str):
    pairs = _PAID_GREEN_PAIRS.fullmatch(text)
    if pairs is not None and (pairs["subject"].casefold() == "this creature"
            or SourceReferenceSpec(source_name).matches(pairs["subject"])):
        return "+1/+1", DynamicEntryCounterAmountSpec(
            value_source=DynamicEntryCounterValueSource.MANA_OF_COLOR_SPENT,
            calculation=DynamicEntryCounterCalculation.PAID_GREEN_PAIRS, coefficient=3, mana_color="G",
        )
    match=re.fullmatch(rf'If at least (?P<minimum>{FIXED_COUNT_PATTERN}) '
        rf'(?P<color>white|blue|black|red|green) mana was spent to cast this spell, '
        rf'(?P<subject>.+?) enters with (?P<amount>a|an|{FIXED_COUNT_PATTERN}) '
        rf'(?P<counter>{FIXED_COUNTER_NAME_PATTERN}) (?P<plural>counter|counters) on it\.?',text,re.I)
    if match is None or (source_self_permanent_type(match['subject']) is None
            and not SourceReferenceSpec(source_name).matches(match['subject'])):
        return None
    minimum=fixed_number(match['minimum']);amount=fixed_number(match['amount'])
    if minimum<1 or not 1<=amount<=10 or (match['plural'].casefold()=='counter')!=(amount==1):
        return None
    return (match['counter'],DynamicEntryCounterAmountSpec(
        value_source=DynamicEntryCounterValueSource.MANA_OF_COLOR_SPENT,
        calculation=DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST,
        coefficient=amount,minimum=minimum,
        mana_color={'white':'W','blue':'U','black':'B','red':'R','green':'G'}[match['color'].casefold()] ))


_PAID_WHITE_TARGETS = re.compile(
    r"choose up to two other target creatures you control\. For each \{W\}\{W\} spent to cast "
    r"this creature, put a \+1/\+1 counter on each of them\.",
    re.I,
)


def paid_white_entry_effect_compiler(record, material_line, *, card_name, ordinary, match_source):
    if record.faces or "{W}{W}" not in material_line:
        return ordinary
    from .draw_templates import trigger_ability_word_material_line

    matched = match_source(trigger_ability_word_material_line(material_line), card_name=card_name)
    if (matched is None or matched["event"].casefold() != "enters"
            or not _PAID_WHITE_TARGETS.fullmatch(matched["body"])):
        return ordinary

    def compile_body(body, **kwargs):
        if not _PAID_WHITE_TARGETS.fullmatch(body):
            return ordinary(body, **kwargs)
        from ..source_entry_cast_information import PAID_WHITE_ENTRY_TARGET_MECHANIC, paid_white_entry_amount
        from .quantified_counter_targets import quantified_counter_target_template

        template, (effect,), schema, mechanics = quantified_counter_target_template(
            "Put a +1/+1 counter on each of up to two target creatures you control.",
        )
        return (template, ({**effect, "amount": paid_white_entry_amount()},),
                {**schema, "source_exclusion": True}, (*mechanics, PAID_WHITE_ENTRY_TARGET_MECHANIC))

    return compile_body
