from __future__ import annotations

"""Fixed temporary removals use existing incarnation-locked layer commits."""

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec,object_query_capabilities
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES

OPERATION='apply_source_characteristics_until_end_of_turn'
MECHANIC='fixed-temporary-keyword-removal'
CAPABILITY='continuous.resolution.fixed_keyword_removal'


def temporary_keyword_removal_instruction(effect):
    fields={'op','schema_version','power','toughness','remove_keywords','keywords'}
    if not isinstance(effect,Mapping) or set(effect) not in (fields|{'card'},fields|{'predicate'}):
        raise ValueError('Temporary keyword removal has a closed selection/result schema')
    if effect['op']!=OPERATION or type(effect['schema_version']) is not int or effect['schema_version']!=11:
        raise ValueError('Temporary keyword removal requires its explicit version')
    if any(type(effect[field]) is not int for field in ('power','toughness')):
        raise ValueError('Temporary removal stat modifiers must be fixed integers')
    for field in ('remove_keywords','keywords'):
        values=effect[field]
        if (not isinstance(values,(list,tuple))
                or any(type(value) is not str or value not in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES for value in values)
                or len(values)!=len(set(values))):
            raise ValueError('Temporary removal keywords require represented fixed abilities')
    if not effect['remove_keywords'] or set(effect['remove_keywords']).intersection(effect['keywords']):
        raise ValueError('Temporary removal requires a nonconflicting removal result')
    if 'card' in effect:
        if type(effect['card']) is not str or not effect['card']:
            raise ValueError('Temporary removal requires its selected incarnation reference')
        return effect['card']
    query=ObjectQuerySpec.from_dict(effect['predicate'])
    if query.to_dict()!=dict(effect['predicate']):
        raise ValueError('Temporary removal query is not canonical')
    return query


def temporary_keyword_removal_capabilities(*,effects,target_schema,mechanic_ids):
    mechanics=set(mechanic_ids)
    if MECHANIC not in mechanics or 'cr-611-continuous-effects' not in mechanics or len(effects)!=1:
        return ()
    try:selection=temporary_keyword_removal_instruction(effects[0])
    except (ValueError,TypeError,KeyError):return ()
    dependencies={CAPABILITY,'continuous.resolution.fixed_characteristics_until_end_of_turn'}
    if isinstance(selection,ObjectQuerySpec):
        from ..compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
        if 'creature' not in selection.types_all or not fixed_resolution_characteristic_query_is_closed(selection,target_schema=target_schema):return ()
        dependencies.update(object_query_capabilities(selection))
    elif selection=='$source.zone_object':
        if target_schema is not None:return ()
    elif selection=='$target.0':
        from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed,direct_target_predicate_capabilities
        if (not isinstance(target_schema,Mapping) or type(target_schema.get('count')) is not int
                or target_schema['count']!=1 or target_schema.get('types_any')!=['creature']
                or not direct_permanent_target_schema_is_closed(target_schema)):return ()
        dependencies.update(direct_target_predicate_capabilities(target_schema))
    else:return ()
    if target_schema is not None:
        if 'cr-115-targets' not in mechanics:return ()
        dependencies.add('target.revalidate_resolution')
    keywords=(*effects[0]['remove_keywords'],*effects[0]['keywords'])
    if not {keyword.casefold() for keyword in keywords}<=mechanics:return ()
    dependencies.update(cap for keyword in keywords for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])
    return tuple(sorted(dependencies))


def temporary_keyword_removal_runtime_dependencies(program):
    if not any(effect.get('op')==OPERATION and effect.get('schema_version')==11 for effect in program.effects):return ()
    dependencies=temporary_keyword_removal_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    if not dependencies:raise ValueError('Temporary keyword removal lacks complete selection/result closure')
    return dependencies


def apply_temporary_keyword_removal(host,effect,*,actor,reason):
    from ..continuous_effect_state import (ResolutionContinuousComponent,create_resolution_continuous_effect_components,
        matching_battlefield_objects,resolution_effect_source)
    from ..continuous_effect_model import ContinuousOperation,Layer
    from ..errors import GameRuleError
    instruction={key:value for key,value in effect.items() if key!='_runtime_source'}
    stale=instruction.get('card',True) is None
    if stale:instruction['card']='stale-resolved-reference'
    try:
        selection=temporary_keyword_removal_instruction(instruction)
        if isinstance(selection,ObjectQuerySpec):
            from .resolution_characteristic_shapes import fixed_resolution_characteristic_set_is_closed
            if not fixed_resolution_characteristic_set_is_closed(selection):raise ValueError('Temporary removal query is unavailable')
            affected=matching_battlefield_objects(host,selection)
        else:
            if stale:return ()
            try:affected=(host._resolve_object(actor,selection,zones={'battlefield'}),)
            except GameRuleError:return ()
        operations=tuple(ContinuousOperation('remove_ability',value) for value in instruction['remove_keywords'])+tuple(
            ContinuousOperation('add_ability',value) for value in instruction['keywords'])
        components=[ResolutionContinuousComponent(Layer.ABILITY,'6',operations)]
        if instruction['power'] or instruction['toughness']:
            components.append(ResolutionContinuousComponent(Layer.POWER_TOUGHNESS,'7c',(
                ContinuousOperation('modify_power_toughness',(instruction['power'],instruction['toughness'])),)))
        create_resolution_continuous_effect_components(host,source=resolution_effect_source(host,effect,fallback_card=affected[0] if affected else None),
            targets=affected,components=tuple(components))
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    refs=tuple(card.ref for card in affected)
    host._log(actor,'permanent.keyword_removal',reason,{'objects':list(refs),'reason':reason},importance=1,changed_objects=[card.object_id for card in affected])
    return refs
