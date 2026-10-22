from __future__ import annotations

"""Fixed source-entry counter clauses over actual colored mana units paid."""

import re

from ..entry_counter_model import DynamicEntryCounterAmountSpec, DynamicEntryCounterCalculation, DynamicEntryCounterValueSource
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .counter_placement_templates import FIXED_COUNTER_NAME_PATTERN
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


def paid_color_entry_amount(text: str, *, source_name: str):
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
