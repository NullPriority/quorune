from __future__ import annotations

"""Closed temporary Defender permission using the canonical duration journal."""

from typing import Mapping
from ..defender_permission import (
    DefenderAttackPermission, DEFENDER_PERMISSION_CAPABILITY,
    DEFENDER_TEMPORARY_CAPABILITY, DEFENDER_TEMPORARY_MECHANIC, DEFENDER_TEMPORARY_OPERATION,
)
from ..ability_fragments import ability_fragment_to_dict
from ..continuous_effects import ContinuousOperation, Layer
from ..continuous_effect_state import (
    ResolutionContinuousComponent, create_resolution_continuous_effect_components,
    matching_battlefield_objects, resolution_effect_source,
)
from ..object_predicate import ObjectQuerySpec
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS, FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..errors import GameRuleError

def decode_temporary_defender_permission(effect):
    required={'op','schema_version','permission','power','toughness','keywords'}
    fields=set(effect)-{'_runtime_source'}
    if fields not in (required|{'card'},required|{'predicate'}):raise ValueError('Temporary Defender permission has a closed schema')
    if effect['op']!=DEFENDER_TEMPORARY_OPERATION or type(effect['schema_version']) is not int or effect['schema_version']!=3:
        raise ValueError('Temporary Defender permission requires version 3')
    if effect['permission']!='ignore_defender' or type(effect['power']) is not int or type(effect['toughness']) is not int:
        raise ValueError('Temporary Defender permission modifier is malformed')
    keywords=effect['keywords']
    if not isinstance(keywords,(list,tuple)) or any(type(k) is not str or k not in FIXED_CHARACTERISTIC_KEYWORDS for k in keywords) or len(set(keywords))!=len(keywords):
        raise ValueError('Temporary Defender permission keywords are unsupported')
    selection=ObjectQuerySpec.from_dict(effect['predicate']) if 'predicate' in effect else effect['card']
    return selection,tuple(keywords)


def temporary_defender_permission_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1 or DEFENDER_TEMPORARY_MECHANIC not in mechanic_ids:return ()
    try:selection,keywords=decode_temporary_defender_permission(effects[0])
    except (KeyError,TypeError,ValueError):return ()
    dependencies={DEFENDER_PERMISSION_CAPABILITY,DEFENDER_TEMPORARY_CAPABILITY,
        'continuous.resolution.fixed_characteristics_until_end_of_turn'}
    if isinstance(selection,ObjectQuerySpec):
        from ..compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
        if 'creature' not in selection.types_all or not fixed_resolution_characteristic_query_is_closed(selection,target_schema=target_schema):return ()
    elif selection=='$source.zone_object':
        if target_schema is not None:return ()
    elif selection=='$target.0':
        from .node_capability_shapes import direct_target_predicate_capabilities
        if dict(target_schema or {})!={'zones':['battlefield'],'categories':['permanent'],'count':1,'types_any':['creature']}:return ()
        dependencies.update(direct_target_predicate_capabilities(target_schema));dependencies.add('target.revalidate_resolution')
    else:return ()
    dependencies.update(cap for keyword in keywords for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])
    return tuple(sorted(dependencies))


def apply_temporary_defender_permission(host,effect,*,actor,reason):
    try:selection,keywords=decode_temporary_defender_permission(effect)
    except (KeyError,TypeError,ValueError) as exc:raise GameRuleError(str(exc)) from exc
    if isinstance(selection,ObjectQuerySpec):
        affected=matching_battlefield_objects(host,selection)
    else:
        try:affected=(host._resolve_object(actor,selection,zones={'battlefield'}),)
        except GameRuleError:return ()
    affected=tuple(card for card in affected if 'creature' in host._type_parts(str(host._effective_card_data(card).get('type_line') or ''))[0])
    ability_operations=(ContinuousOperation('add_ability_fragment',ability_fragment_to_dict(DefenderAttackPermission())),
        *(ContinuousOperation('add_ability',keyword) for keyword in keywords))
    components=[ResolutionContinuousComponent(layer=Layer.ABILITY,sublayer='6',operations=ability_operations)]
    if effect['power'] or effect['toughness']:
        components.append(ResolutionContinuousComponent(layer=Layer.POWER_TOUGHNESS,sublayer='7c',
            operations=(ContinuousOperation('modify_power_toughness',[effect['power'],effect['toughness']]),)))
    create_resolution_continuous_effect_components(host,source=resolution_effect_source(host,effect,fallback_card=affected[0] if affected else None),targets=affected,components=tuple(components))
    host._log(actor,'permanent.defender_permission',f'{reason}: selected creatures can attack with Defender this turn.',
        {'objects':[card.ref for card in affected],'reason':reason},importance=1,changed_objects=[card.object_id for card in affected])
    return tuple(card.ref for card in affected)
