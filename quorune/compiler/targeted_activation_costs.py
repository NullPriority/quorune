from __future__ import annotations

"""Target-conditioned costs compose existing source and target query grammars."""

import re

from ..activation_cost_modifiers import (
    ACTIVATION_GENERIC_COST_EVENT, TARGETED_ACTIVATION_COST_CAPABILITY,
    TARGETED_ACTIVATION_COST_HANDLER, FixedActivationCostModifierSpec,
)
from ..object_predicate import ObjectQuerySpec, object_query_capabilities
from .activation_cost_modifier_templates import _modifier_subject
from .target_cost_subjects import target_cost_subject


def targeted_activation_cost_template(text, *, source_name):
    material = ' '.join(text.strip().replace('’', "'").split())
    own = re.fullmatch(r"This Equipment's equip ability costs \{(?P<amount>[1-9]\d*)\} less to activate if it targets (?P<target>.+?)\.?", material, re.I)
    match = re.fullmatch(r'(?P<head>.+?) that target (?P<target>.+?) cost \{(?P<amount>[1-9]\d*)\} (?P<direction>more|less) to activate\.?', material, re.I)
    if own is not None:
        subject = ('any', 'any', 'equip', ObjectQuerySpec())
        amount = -int(own['amount']); target = own['target']
    elif match is not None:
        subject = _modifier_subject(match['head'])
        if match['head'].casefold() == 'abilities your opponents activate':
            subject = ('any', 'source_opponents', 'all', ObjectQuerySpec())
        if subject is None:
            return None
        amount = int(match['amount']) * (1 if match['direction'].casefold() == 'more' else -1)
        target = match['target']
    else:
        return None
    condition = target_cost_subject(target, source_name=source_name)
    if condition is None:
        return None
    relation, controller, scope, predicate = subject
    spec = FixedActivationCostModifierSpec(amount, predicate, relation, controller, scope,
        schema_version=2, target_condition=condition, source_is_provider=own is not None)
    return ('target-dependent-activation-cost-v2', {'handler_id': TARGETED_ACTIVATION_COST_HANDLER,
        'schema_version': 2, 'event': ACTIVATION_GENERIC_COST_EVENT, 'modifier': spec.to_dict()},
        (TARGETED_ACTIVATION_COST_CAPABILITY, *object_query_capabilities(spec.to_dict())))
