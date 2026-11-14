"""Independent closure of a recipient, controlled subject and source role."""
from collections.abc import Mapping
from ..recipient_control_model import (RECIPIENT_CONTROL_CAPABILITY,RECIPIENT_CONTROL_MECHANIC,
    GIVE_CONTROL_OPERATION,CHOOSE_CONTROL_RECIPIENT_OPERATION,validate_recipient_control_effect,
    GIVE_CONTROL_SET_OPERATION,validate_recipient_control_set_effect)
from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed,direct_target_predicate_capabilities


def _player_schema(schema):
    return isinstance(schema,Mapping) and type(schema.get('count')) is int and any(schema=={'zones':['player'],'categories':['player'],'player_relation':relation,'count':1} for relation in ('any','opponent'))


def recipient_control_node_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1 or not {RECIPIENT_CONTROL_MECHANIC,'cr-611-continuous-effects'}<=set(mechanic_ids):return ()
    effect=effects[0]
    if effect.get('op')==GIVE_CONTROL_SET_OPERATION:
        try:validate_recipient_control_set_effect(effect)
        except (TypeError,ValueError):return ()
        if (effect['controller']!='$target.0' or effect['source']!='$source' or effect['exclude_source']!='$source.zone_object'
                or 'cr-115-targets' not in mechanic_ids or not _player_schema(target_schema)):return ()
        return (RECIPIENT_CONTROL_CAPABILITY,'continuous.control.fixed_resolution','target.revalidate_resolution')
    if effect.get('op')==CHOOSE_CONTROL_RECIPIENT_OPERATION:
        if type(effect.get('schema_version')) is int and effect['schema_version'] in {2,3}:
            version=effect['schema_version'];opponent_only=version!=3
            expected={'op':CHOOSE_CONTROL_RECIPIENT_OPERATION,'schema_version':version,'player':'$controller','card':'$target.0','opponent_only':opponent_only}
            if (dict(effect)!=expected or type(effect['schema_version']) is not int or effect['opponent_only'] is not opponent_only
                    or 'cr-115-targets' not in mechanic_ids or not direct_permanent_target_schema_is_closed(target_schema)):return ()
            return tuple(sorted({RECIPIENT_CONTROL_CAPABILITY,'continuous.control.fixed_resolution','target.revalidate_resolution',
                *direct_target_predicate_capabilities(target_schema)}))
        expected={'op':CHOOSE_CONTROL_RECIPIENT_OPERATION,'schema_version':1,'player':'$controller','card':'$source.zone_object','opponent_only':True}
        if dict(effect)!=expected or type(effect.get('schema_version')) is not int or effect.get('opponent_only') is not True or target_schema is not None:return ()
        return (RECIPIENT_CONTROL_CAPABILITY,'continuous.control.fixed_resolution')
    try:validate_recipient_control_effect(effect)
    except (ValueError,TypeError):return ()
    if effect['schema_version']==3:
        from ..player_maximum_model import PLAYER_MAXIMUM_CAPABILITY,PLAYER_MAXIMUM_MECHANIC
        if (effect['source']!='$source' or effect['card']!='$source.zone_object' or target_schema is not None
                or PLAYER_MAXIMUM_MECHANIC not in mechanic_ids):return ()
        return (RECIPIENT_CONTROL_CAPABILITY,'continuous.control.fixed_resolution',PLAYER_MAXIMUM_CAPABILITY)
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
    def visit(value,schema):
        if isinstance(value,Mapping):
            if 'target_schema' in value:schema=value['target_schema']
            if value.get('op')==GIVE_CONTROL_OPERATION:
                validate_recipient_control_effect(value)
                if value['schema_version']==3:
                    from .player_maximum_shapes import player_maximum_control_dependencies
                    if value['source']!='$source' or value['card']!='$source.zone_object':
                        raise ValueError('Unique maximum control lost its original source role')
                    required.update(player_maximum_control_dependencies(program,value['controller']))
                elif value['schema_version']==2:
                    from ..target_controller_information import TARGET_CONTROLLER_INFORMATION_CAPABILITY
                    if value['source']!='$source' or value['card']!='$source.zone_object':
                        raise ValueError('Target-controller control requires its original source subject')
                    required.add(TARGET_CONTROLLER_INFORMATION_CAPABILITY)
                elif value['source']!='$source' or value['controller']!='$target.0' or value['card'] not in {'$target.1','$source.zone_object'}:
                    raise ValueError('Recipient control payload lost its subject or recipient role')
                required.add(RECIPIENT_CONTROL_CAPABILITY)
            elif value.get('op')==CHOOSE_CONTROL_RECIPIENT_OPERATION:
                version=value.get('schema_version')
                if type(version) is not int or version not in {1,2,3}:raise ValueError('Recipient control choice schema is unsupported')
                card='$target.0' if version in {2,3} else '$source.zone_object';opponent_only=version!=3
                expected={'op':CHOOSE_CONTROL_RECIPIENT_OPERATION,'schema_version':version,'player':'$controller','card':card,'opponent_only':opponent_only}
                if dict(value)!=expected or type(value['schema_version']) is not int or value['opponent_only'] is not opponent_only:
                    raise ValueError('Recipient control choice payload lost its bound scope')
                if version in {2,3} and not direct_permanent_target_schema_is_closed(schema):
                    raise ValueError('Target-subject control choice lost its closed permanent target schema')
                required.add(RECIPIENT_CONTROL_CAPABILITY)
            elif value.get('op')==GIVE_CONTROL_SET_OPERATION:
                validate_recipient_control_set_effect(value)
                if value['controller']!='$target.0' or value['source']!='$source' or value['exclude_source']!='$source.zone_object':
                    raise ValueError('Recipient control set lost its recipient or original source')
                if not _player_schema(schema):raise ValueError('Recipient control set lost its closed player target schema')
                required.add(RECIPIENT_CONTROL_CAPABILITY)
            for child in value.values():visit(child,schema)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child,schema)
    visit((program.effects,program.target_schema),program.target_schema)
    return tuple(sorted(required))
