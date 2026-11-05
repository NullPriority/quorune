from __future__ import annotations

"""Closed unconditional legend-rule exemption text; no runtime text reads."""

import re

from ..creature_subtypes import canonical_creature_subtype_surface
from ..legend_rule_model import LegendRuleExemptionSpec
from ..legend_rule_model import LEGEND_RULE_CAPABILITY, LEGEND_RULE_HANDLER
from ..object_predicate import ObjectQuerySpec


def legend_rule_exemption_spec(text: str) -> LegendRuleExemptionSpec | None:
    normalized = text.replace('’', "'").replace('“', '"').replace('”', '"').strip()
    match = re.fullmatch(r'The "legend rule" doesn\'t apply(?: to (.+))?\.?', normalized, re.I)
    if match is None:
        return None
    subject = match.group(1)
    if subject is None:
        return LegendRuleExemptionSpec('global', ObjectQuerySpec(zones=('battlefield',)))
    subject = subject.removesuffix('.').casefold()
    fixed = {
        'permanents you control': {},
        'creatures you control': {'types_all': ('creature',)},
        'tokens you control': {'token': True},
        'creature tokens you control': {'types_all': ('creature',), 'token': True},
        'nontoken permanents you control': {'token': False},
        'nontoken creatures you control': {'types_all': ('creature',), 'token': False},
    }
    if subject in fixed:
        return LegendRuleExemptionSpec('controller', ObjectQuerySpec(zones=('battlefield',), **fixed[subject]))
    if subject.endswith(' you control'):
        subtype = canonical_creature_subtype_surface(subject.removesuffix(' you control'))
        if subtype is not None:
            return LegendRuleExemptionSpec('controller', ObjectQuerySpec(
                zones=('battlefield',), subtypes_all=(subtype,),
            ))
    return None


def static_legend_rule_exemption_handler(text: str):
    from ..ability_fragments import ability_fragment_to_dict

    spec = legend_rule_exemption_spec(text)
    if spec is None:
        return None
    return (
        'static-legend-rule-exemption-v1',
        {'handler_id': LEGEND_RULE_HANDLER, 'schema_version': 1, 'event': 'continuous',
         'fragment': ability_fragment_to_dict(spec)},
        LEGEND_RULE_CAPABILITY,
    )
