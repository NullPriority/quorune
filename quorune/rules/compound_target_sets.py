"""Typed target-role closure over the existing simultaneous set operations."""
from collections.abc import Mapping

COMPOUND_TARGET_SET_CAPABILITY = 'resolution.effect.fixed_compound_target_set'
COMPOUND_TARGET_SET_MECHANIC = 'fixed-compound-target-set'
_OPS = {'destroy_targets','exile_permanent_targets','return_permanent_targets_to_owner_hand'}


def compound_target_payload_dependencies(effect):
    if effect.get('op') not in _OPS:return ()
    required = set()
    if 'target_roles' in effect:
        if (effect['target_roles'] is not True or effect.get('cards') != '$targets'
                or type(effect.get('maximum_targets')) is not int or not 2 <= effect['maximum_targets'] <= 4
                or set(effect)-{'op','cards','maximum_targets','target_roles','regeneration_prohibited','reason','_replacement_selections'}):
            raise ValueError('Compound target roles require their exact bounded selector')
        required.add(COMPOUND_TARGET_SET_CAPABILITY)
    if 'regeneration_prohibited' in effect:
        if effect['op'] != 'destroy_targets' or effect['regeneration_prohibited'] is not True:
            raise ValueError('Target-set regeneration prohibition requires explicit true destruction')
        required.add('permanent.destroy.regeneration_prohibition')
    return tuple(sorted(required))


def compound_target_set_node_capabilities(*,effects,target_schema,mechanic_ids):
    from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities
    if (COMPOUND_TARGET_SET_MECHANIC not in mechanic_ids or len(effects) != 1
            or not isinstance(target_schema,Mapping) or set(target_schema) != {'groups'}):return ()
    effect, = effects;groups = target_schema['groups']
    fields = {'op','cards','maximum_targets','target_roles'}
    if 'regeneration_prohibited' in effect:fields.add('regeneration_prohibited')
    if (set(effect) != fields or not isinstance(groups,(list,tuple)) or not 2 <= len(groups) <= 4
            or effect.get('maximum_targets') != len(groups)):return ()
    try:required = set(compound_target_payload_dependencies(effect))
    except ValueError:return ()
    if COMPOUND_TARGET_SET_CAPABILITY not in required:return ()
    ids = set()
    for group in groups:
        if not isinstance(group,Mapping):return ()
        schema = dict(group);name = schema.pop('id',None)
        if (type(name) is not str or not name or name in ids
                or type(schema.get('count')) is not int or schema['count'] != 1):return ()
        ids.add(name);schema['count'] = 2
        prototype = {'op':effect['op'],'cards':'$targets','maximum_targets':2}
        caps = fixed_homogeneous_target_set_node_capabilities(effects=(prototype,),target_schema=schema,
            mechanic_ids=(*mechanic_ids,'fixed-homogeneous-target-set'))
        if not caps:return ()
        required.update(caps)
    return tuple(sorted(required))
