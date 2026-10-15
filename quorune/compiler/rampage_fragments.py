from __future__ import annotations

import re
from ..ability_fragments import CombatKeywordTriggerKind, CombatKeywordTriggerSpec, ability_fragment_to_dict
from .ability_keyword_fragment_model import AbilityKeywordFragmentLowering


def rampage_fragment_lowering(material_line, mechanics):
    matches = tuple(match for part in re.split(r'[,;]', material_line.rstrip('.'))
        if (match := re.fullmatch(r'Rampage (?P<amount>[1-9]\d*)', part.strip(), re.I)) is not None)
    if mechanics.count('rampage') != len(matches):
        return AbilityKeywordFragmentLowering(residual_kind='unsupported_rampage_value',
            residual_reason='Rampage requires a fixed positive printed integer.',
            residual_blockers=('fixed Rampage value',))
    return AbilityKeywordFragmentLowering(handlers=tuple({'handler_id': 'ability.trigger.rampage.v1',
        'schema_version': 1, 'event': 'continuous', 'fragment': ability_fragment_to_dict(
            CombatKeywordTriggerSpec(CombatKeywordTriggerKind.RAMPAGE, int(match['amount'])))} for match in matches))
