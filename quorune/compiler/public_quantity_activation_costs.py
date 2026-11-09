from __future__ import annotations

"""Public activation reductions from providers and ability-local counts."""

from dataclasses import replace
import re

from ..activation_cost_modifiers import (FixedActivationCostModifierSpec, ACTIVATION_GENERIC_COST_EVENT,
    PUBLIC_QUANTITY_ACTIVATION_COST_HANDLER, PUBLIC_QUANTITY_ACTIVATION_COST_CAPABILITY,
    validate_ability_local_mana_reduction)
from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from ..rules.public_value_costs import public_value_cost_dependencies
from ..object_predicate import ObjectQuerySpec
from ..self_cast_reductions import (CastReductionMetric, CastReductionMetricKind,
    CastReductionObjectQuery, CastReductionQueryScope)
from .public_value_costs import source_power_cost_metric
from .public_state_queries import fixed_battlefield_query_subject
from .activation_cost_modifier_templates import _modifier_subject
from .query_characteristic_templates import query_characteristic_quantity


_ABILITY_LOCAL_COUNT = re.compile(
    r'\.\s+This ability costs \{1\} less to activate for each (?P<count>[^.]+)\.', re.I,
)


def _activation_discount_quantity(text, *, source_name):
    material = ' '.join(text.casefold().split())
    if re.fullmatch(r'instant (?:and|or|and/or) sorcery cards? in your graveyard', material):
        query = ObjectQuerySpec(zones=('graveyard',), types_any=('instant', 'sorcery'), token=False)
    elif material == 'legendary creature and planeswalker you control':
        query = ObjectQuerySpec(zones=('battlefield',), types_any=('creature', 'planeswalker'),
                                supertypes_all=('legendary',))
    else:
        return query_characteristic_quantity(text, source_name=source_name)
    return CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE, query=query)


def compile_ability_local_mana_reduction(ability, *, source_name):
    material = ability.effect_text
    if (' less to activate for each ' not in material.casefold() or ability.mana_ability
            or ability.zones != ('battlefield',) or ability.complex_symbols
            or ability.mana_cost_options or ability.announced_x_symbols is not None
            or not ability.mana.get('GENERIC')):
        return ability
    matches = tuple(_ABILITY_LOCAL_COUNT.finditer(material))
    if len(matches) != 1:
        return ability
    match, = matches
    quantity = _activation_discount_quantity(match['count'], source_name=source_name)
    try:
        validate_ability_local_mana_reduction(quantity)
    except ValueError:
        return ability
    return replace(ability, effect_text=(material[:match.start() + 1] + material[match.end():]).strip(),
                   generic_reduction_per_legendary_creature=0, ability_local_mana_reduction=quantity)


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
