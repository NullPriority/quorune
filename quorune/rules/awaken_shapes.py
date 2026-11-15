"""Independently validate the ordinary body and exact paid Awaken augmentation."""
from collections.abc import Mapping
from ..awaken_model import (
    AWAKEN_CAPABILITY,AWAKEN_PAID_MECHANIC,FixedManaAwakenSpec,
)
from ..spell_text_variants import spell_body_fingerprint
from ..target_clause_binding import scope_clause_effect,clause_target_roles_are_exact
from ..resolution_characteristic_model import (
    CHARACTERISTIC_OPERATION,INDEFINITE_TARGET_LAND_ANIMATION_VERSION,INDEFINITE_TARGET_LAND_ANIMATION_MECHANIC,
    FixedResolutionCharacteristicsSpec,IndefiniteTargetLandAnimationSpec,
)


def awaken_land_target_schema():
    return {'zones':['battlefield'],'categories':['permanent'],'count':1,'types_any':['land'],'controller_relation':'you'}


def awaken_result_effects(count,reference):
    if type(count)is not int or count<=0:raise ValueError('Awaken counters require a positive fixed count')
    settings=FixedResolutionCharacteristicsSpec(card_types=('Creature',),creature_subtypes=('Elemental',),
        retain_types=True,retain_creature_subtypes=True,keywords=('Haste',),base_power=0,base_toughness=0)
    animation={'op':CHARACTERISTIC_OPERATION,'schema_version':INDEFINITE_TARGET_LAND_ANIMATION_VERSION,
               'card':reference,'characteristics':IndefiniteTargetLandAnimationSpec(settings).to_dict()}
    return ({'op':'place_counters','card':reference,'counter':'+1/+1','amount':count,'source':'$source'},animation)


def ordinary_awaken_body(spec,effects,target_schema):
    if not isinstance(spec,FixedManaAwakenSpec) or not isinstance(effects,(list,tuple)) or len(effects)<3:
        raise ValueError('Paid Awaken requires its original body followed by both land results')
    if spec.normal_targeted:
        if not isinstance(target_schema,Mapping) or set(target_schema)!={'groups'}:
            raise ValueError('Paid Awaken requires its two original target groups')
        groups=target_schema['groups']
        if not isinstance(groups,(list,tuple)) or len(groups)!=2 or any(not isinstance(group,Mapping) for group in groups):
            raise ValueError('Paid Awaken requires one original target and one added land target')
        original,land=groups;expected_land={key:value for key,value in awaken_land_target_schema().items() if key!='count'}
        if (land!={**expected_land,'id':'clause_1','min':1,'max':1}
                or type(land.get('min'))is not int or type(land.get('max'))is not int):
            raise ValueError('Awaken requires exactly one controlled land target')
        if (original.get('id')!='clause_0' or type(original.get('min'))is not int or original['min']!=1
                or type(original.get('max'))is not int or original['max']!=1):
            raise ValueError('Awaken original scalar target group is malformed')
        schema={key:value for key,value in original.items() if key not in {'id','min','max'}};schema['count']=1
        if not all(clause_target_roles_are_exact(effect,0) for effect in effects[:-2]):
            raise ValueError('Awaken original body escaped its target group')
        body=tuple(scope_clause_effect(effect,0,restore=True) for effect in effects[:-2]);reference='$target_group.clause_1.0'
    else:
        if target_schema!=awaken_land_target_schema() or type(target_schema.get('count'))is not int:
            raise ValueError('Awaken has one added land target')
        body=tuple(dict(effect) for effect in effects[:-2]);schema=None;reference='$target.0'
    if (spell_body_fingerprint(effects[-2:],None)!=spell_body_fingerprint(awaken_result_effects(spec.count,reference),None)
            or spell_body_fingerprint(body,schema)!=spec.normal_fingerprint
            or spell_body_fingerprint(effects,target_schema)!=spec.awakened_fingerprint):
        raise ValueError('Awaken price, count and source-correlated spell bodies differ')
    return body,schema


def awaken_node_capabilities(*,effects,target_schema,mechanics,cost_schema):
    if not isinstance(cost_schema,Mapping) or 'awaken' not in cost_schema:
        return () if AWAKEN_PAID_MECHANIC in mechanics else None
    if set(cost_schema)!={'awaken'}:return ()
    try:
        spec=FixedManaAwakenSpec.from_dict(cost_schema['awaken'])
        if spell_body_fingerprint(effects,target_schema)==spec.normal_fingerprint:
            return () if AWAKEN_PAID_MECHANIC in mechanics or spec.normal_targeted!=(target_schema is not None) else None
        body,schema=ordinary_awaken_body(spec,effects,target_schema)
    except (TypeError,ValueError,KeyError):return ()
    from .capabilities import capability_dependencies_for_node
    from .node_capability_shapes import fixed_counter_placement_node_capabilities
    from .resolution_characteristic_shapes import fixed_resolution_characteristic_capabilities
    base=capability_dependencies_for_node(effects=body,target_schema=schema,mechanic_ids=spec.normal_mechanics)
    counter,animation=awaken_result_effects(spec.count,'$target.0');land=awaken_land_target_schema()
    counters=fixed_counter_placement_node_capabilities(effects=(counter,),target_schema=land,
        mechanic_ids=('cr-122-counters','cr-115-targets'))
    animated=fixed_resolution_characteristic_capabilities(effects=(animation,),target_schema=land,
        mechanic_ids=('cr-611-continuous-effects','cr-115-targets',INDEFINITE_TARGET_LAND_ANIMATION_MECHANIC))
    return tuple(sorted({AWAKEN_CAPABILITY,*base,*counters,*animated})) if base and counters and animated else ()
