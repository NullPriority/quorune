"""Independent closure of a recipient, controlled subject and source role."""
from collections.abc import Mapping
from ..recipient_control_model import (RECIPIENT_CONTROL_CAPABILITY,RECIPIENT_CONTROL_MECHANIC,
    GIVE_CONTROL_OPERATION,CHOOSE_CONTROL_RECIPIENT_OPERATION,validate_recipient_control_effect)
from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed,direct_target_predicate_capabilities


def _player_schema(schema):
    return isinstance(schema,Mapping) and type(schema.get('count')) is int and any(schema=={'zones':['player'],'categories':['player'],'player_relation':relation,'count':1} for relation in ('any','opponent'))


def recipient_control_node_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1 or not {RECIPIENT_CONTROL_MECHANIC,'cr-611-continuous-effects'}<=set(mechanic_ids):return ()
    effect=effects[0]
    if effect.get('op')==CHOOSE_CONTROL_RECIPIENT_OPERATION:
        expected={'op':CHOOSE_CONTROL_RECIPIENT_OPERATION,'schema_version':1,'player':'$controller','card':'$source.zone_object','opponent_only':True}
        if dict(effect)!=expected or type(effect.get('schema_version')) is not int or effect.get('opponent_only') is not True or target_schema is not None:return ()
        return (RECIPIENT_CONTROL_CAPABILITY,'continuous.control.fixed_resolution')
    try:validate_recipient_control_effect(effect)
    except (ValueError,TypeError):return ()
    if effect['controller']!='$target.0' or effect['source']!='$source' or 'cr-115-targets' not in mechanic_ids:return ()
    required={RECIPIENT_CONTROL_CAPABILITY,'continuous.control.fixed_resolution','target.revalidate_resolution'}
    if effect['card']=='$source.zone_object':
        return tuple(sorted(required)) if _player_schema(target_schema) else ()
    if effect['card']!='$target.1' or not isinstance(target_schema,Mapping) or set(target_schema)!={'groups'}:return ()
    groups=target_schema['groups']
    if not isinstance(groups,(list,tuple)) or len(groups)!=2 or any(not isinstance(g,Mapping) for g in groups):return ()
    if groups[0].get('id')!='recipient' or groups[1].get('id')!='permanent':return ()
    if not _player_schema({k:v for k,v in groups[0].items() if k!='id'}):return ()
    subject={k:v for k,v in groups[1].items() if k!='id'}
    if not direct_permanent_target_schema_is_closed(subject):return ()
    required.update(direct_target_predicate_capabilities(subject))
    return tuple(sorted(required))


def recipient_control_runtime_dependencies(program):
    """Retain recipient authority from payloads even without coverage labels."""
    required=set()
    def visit(value):
        if isinstance(value,Mapping):
            if value.get('op')==GIVE_CONTROL_OPERATION:
                validate_recipient_control_effect(value)
                if value['schema_version']==2:
                    from ..target_controller_information import TARGET_CONTROLLER_INFORMATION_CAPABILITY
                    if value['source']!='$source' or value['card']!='$source.zone_object':
                        raise ValueError('Target-controller control requires its original source subject')
                    required.add(TARGET_CONTROLLER_INFORMATION_CAPABILITY)
                elif value['source']!='$source' or value['controller']!='$target.0' or value['card'] not in {'$target.1','$source.zone_object'}:
                    raise ValueError('Recipient control payload lost its subject or recipient role')
                required.add(RECIPIENT_CONTROL_CAPABILITY)
            elif value.get('op')==CHOOSE_CONTROL_RECIPIENT_OPERATION:
                expected={'op':CHOOSE_CONTROL_RECIPIENT_OPERATION,'schema_version':1,'player':'$controller','card':'$source.zone_object','opponent_only':True}
                if dict(value)!=expected or type(value['schema_version']) is not int or value['opponent_only'] is not True:
                    raise ValueError('Recipient control choice payload lost its bound scope')
                required.add(RECIPIENT_CONTROL_CAPABILITY)
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit((program.effects,program.target_schema))
    return tuple(sorted(required))
