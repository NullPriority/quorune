from __future__ import annotations

"""Public source-power and controlled permanent-count activation reductions."""

import re

from ..activation_cost_modifiers import (FixedActivationCostModifierSpec, ACTIVATION_GENERIC_COST_EVENT,
    PUBLIC_QUANTITY_ACTIVATION_COST_HANDLER, PUBLIC_QUANTITY_ACTIVATION_COST_CAPABILITY)
from ..rules.public_value_costs import public_value_cost_dependencies
from ..object_predicate import ObjectQuerySpec
from ..self_cast_reductions import (CastReductionMetric, CastReductionMetricKind,
    CastReductionObjectQuery, CastReductionQueryScope)
from .public_value_costs import source_power_cost_metric
from .public_state_queries import fixed_battlefield_query_subject
from .activation_cost_modifier_templates import _modifier_subject


def public_quantity_activation_cost_template(text, *, source_name):
    material = ' '.join(text.strip().replace('’', "'").split())
    power = re.fullmatch(r"(?P<head>.+?) costs? \{X\} less to activate, where X is (?P<metric>.+?)\."
        r"(?P<minimum> This effect can't reduce the (?:amount of )?mana in (?:that|this) cost to less than one mana\.)?", material, re.I)
    count = re.fullmatch(r'(?P<head>Boast abilities you activate) cost \{(?P<amount>[1-9][0-9]*)\} less to activate for each (?P<query>.+?)\.?', material, re.I)
    if power is not None:
        subject = _modifier_subject(power['head'])
        metric = source_power_cost_metric(power['metric'], source_name=source_name)
        amount, minimum = -1, int(bool(power['minimum']))
    elif count is not None:
        parsed = fixed_battlefield_query_subject(count['query'])
        if parsed is None:
            singular = re.fullmatch(r'(?P<kind>[A-Za-z]+) you control', count['query'], re.I)
            if singular is not None:
                parsed = fixed_battlefield_query_subject(singular['kind'] + 's you control')
        if parsed is None or parsed[0] != 'source_controller' or parsed[2]:
            return None
        subject = ('any', 'source_controller', 'boast', ObjectQuerySpec())
        metric = CastReductionMetric(CastReductionMetricKind.OBJECT_COUNT, schema_version=2,
            queries=(CastReductionObjectQuery(CastReductionQueryScope.CONTROLLER_ZONE, parsed[1]),))
        amount, minimum = -int(count['amount']), 0
    else:
        return None
    if subject is None or metric is None:
        return None
    relation, controller, scope, predicate = subject
    spec = FixedActivationCostModifierSpec(amount, predicate=predicate, subject_relation=relation,
        controller_relation=controller, ability_scope=scope, minimum_mana=minimum,
        schema_version=3, multiplier=metric.to_dict())
    dependencies = {PUBLIC_QUANTITY_ACTIVATION_COST_CAPABILITY, *public_value_cost_dependencies(metric.to_dict())}
    if scope == 'boast':
        from ..boast import BOAST_CAPABILITY
        dependencies.add(BOAST_CAPABILITY)
    return 'public-quantity-activation-cost-v3', {
        'handler_id': PUBLIC_QUANTITY_ACTIVATION_COST_HANDLER, 'schema_version': 3,
        'event': ACTIVATION_GENERIC_COST_EVENT, 'modifier': spec.to_dict()}, tuple(sorted(dependencies))
