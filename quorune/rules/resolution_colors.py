"""Fixed color scopes through canonical targets and the continuous journal."""
from collections.abc import Mapping

from ..resolution_color_model import (
    TARGET_COLOR_VERSIONS,TARGET_COLOR_SET_VERSION,TARGET_COLOR_SCOPE_CAPABILITY,
    TARGET_COLOR_SCOPE_MECHANIC,decode_resolution_color,
)
from ..resolution_characteristic_model import FIXED_TARGET_COLOR_CAPABILITY,fixed_target_color_schema


def resolution_color_node_capabilities(*,effects,target_schema,mechanic_ids):
    chosen=[e for e in effects if e.get('op')=='apply_source_characteristics_until_end_of_turn' and e.get('schema_version') in TARGET_COLOR_VERSIONS]
    if not chosen or TARGET_COLOR_SCOPE_MECHANIC not in mechanic_ids:return ()
    from ..targets import target_plan
    from ..target_cardinality import AnnouncedTargetCardinality,bind_any_number_target_schema,ANY_NUMBER_TARGET_CAPABILITY
    from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed,direct_target_predicate_capabilities
    schema=target_schema;extra=set()
    if not isinstance(schema,Mapping):return ()
    if any(e['schema_version']==TARGET_COLOR_SET_VERSION for e in chosen):
        if len(effects)!=1 or chosen[0].get('announced_target_count') is not True:return ()
        try:
            count=AnnouncedTargetCardinality.from_dict(schema['count_parameter'])
            if count.schema_version!=3:return ()
            schema=bind_any_number_target_schema(schema,2);schema.pop('min');schema.pop('max');schema['count']=1
            extra.add(ANY_NUMBER_TARGET_CAPABILITY)
        except (ValueError,TypeError,KeyError):return ()
    if schema!=fixed_target_color_schema() and schema!={'zones':['stack'],'categories':['spell'],'count':1}:
        if not direct_permanent_target_schema_is_closed(schema):return ()
        extra.update(direct_target_predicate_capabilities(schema))
    try:
        plan=target_plan(schema)
        if len(plan.groups)!=1 or plan.groups[0].min_targets!=1 or plan.groups[0].max_targets!=1:return ()
        for effect in chosen:decode_resolution_color(effect)
    except (ValueError,TypeError,KeyError):return ()
    required={TARGET_COLOR_SCOPE_CAPABILITY,FIXED_TARGET_COLOR_CAPABILITY,'target.revalidate_resolution',*extra}
    from .fixed_effect_clause_shapes import closed_effect_component_capabilities
    from .capabilities import capability_dependencies_for_node
    for effect in effects:
        if effect in chosen:continue
        target=target_schema if '$target' in str(effect) else None
        owned=closed_effect_component_capabilities((effect,),target_schema=target,mechanics=set(mechanic_ids)-{TARGET_COLOR_SCOPE_MECHANIC})
        if not owned:return ()
        required.update(owned)
        required.update(capability_dependencies_for_node(effects=(effect,),target_schema=target,
            mechanic_ids=set(mechanic_ids)-{TARGET_COLOR_SCOPE_MECHANIC}))
    return tuple(sorted(required))


def resolution_color_runtime_dependencies(program):
    required=set()
    def visit(value):
        if isinstance(value,Mapping):
            if value.get('op')=='apply_source_characteristics_until_end_of_turn' and value.get('schema_version') in TARGET_COLOR_VERSIONS:
                owned=resolution_color_node_capabilities(effects=(value,),target_schema=program.target_schema,
                    mechanic_ids=(TARGET_COLOR_SCOPE_MECHANIC,))
                if not owned:raise ValueError('Scoped color requires its closed target and cardinality authority')
                required.update(owned);return
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))


def apply_resolution_color(host,effect,*,actor,reason):
    from ..continuous_effect_state import ResolutionEffectSource,create_resolution_continuous_effect
    from ..continuous_effects import ContinuousOperation,Layer
    from ..resolution_characteristic_model import FIXED_TARGET_COLOR_ZONES
    from ..errors import GameRuleError
    try:refs,colors,duration,mode=decode_resolution_color(effect,materialized=True)
    except (ValueError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
    cards=[]
    for reference in refs:
        item=next((s for s in host.state.stack if s.ref==reference),None)
        if item is None:card=host._resolve_object(actor,reference,zones={'battlefield'})
        else:
            card=host.state.cards.get(item.card_object_id or '')
            if item.kind not in {'spell','spell_copy'} or card is None or card.zone!='stack':raise GameRuleError('Color target is not a current spell')
        cards.append(card)
    if not cards:return []
    operation=ContinuousOperation('add_colors' if mode=='add' else 'set_colors',colors) if colors else ContinuousOperation('remove_all_colors')
    applied=create_resolution_continuous_effect(host,source=ResolutionEffectSource.from_effect(effect),targets=tuple(cards),
        layer=Layer.COLOR,sublayer='5',operations=(operation,),duration=duration,zones=FIXED_TARGET_COLOR_ZONES)
    if applied is None:raise GameRuleError('Color change requires the continuous-effect journal')
    host._log(actor,'effect.color','The selected objects changed color.',{'targets':list(refs),'colors':list(colors),'color_mode':mode,'duration':duration.value},
        changed_objects=[c.object_id for c in cards],importance=1)
    return list(refs)
