"""Typed target-role closure over the existing simultaneous set operations."""
from collections.abc import Mapping

COMPOUND_TARGET_SET_CAPABILITY = 'resolution.effect.fixed_compound_target_set'
COMPOUND_TARGET_SET_MECHANIC = 'fixed-compound-target-set'
_OPS = {'destroy_targets','exile_permanent_targets','return_permanent_targets_to_owner_hand'}


OPTIONAL_DESTROY_ROLES_VERSION_FIELD = 'optional_destroy_roles_version'


def optional_destroy_roles_schema():
    return {'groups': [
        {'id': f'role_{index}', 'zones': ['battlefield'], 'categories': ['permanent'],
         'types_any': [kind], 'up_to': 1}
        for index, kind in enumerate(('planeswalker', 'artifact'))
    ]}


def optional_destroy_roles_payload_is_closed(effect):
    version = effect.get(OPTIONAL_DESTROY_ROLES_VERSION_FIELD)
    maximum = effect.get('maximum_targets')
    return (type(version) is int and version == 1 and effect.get('op') == 'destroy_targets'
            and type(maximum) is int and maximum == 2 and effect.get('target_roles') is True
            and 'regeneration_prohibited' not in effect)


def compound_target_payload_dependencies(effect):
    if OPTIONAL_DESTROY_ROLES_VERSION_FIELD in effect and not optional_destroy_roles_payload_is_closed(effect):
        raise ValueError('Optional destruction requires one exact version and two separate roles')
    if effect.get('op') not in _OPS:return ()
    required = set()
    if 'target_roles' in effect:
        if (effect['target_roles'] is not True or effect.get('cards') != '$targets'
                or type(effect.get('maximum_targets')) is not int or not 2 <= effect['maximum_targets'] <= 4
                or set(effect)-{'op','cards','maximum_targets','target_roles','regeneration_prohibited','reason',
                    '_replacement_selections', OPTIONAL_DESTROY_ROLES_VERSION_FIELD}):
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
    optional = OPTIONAL_DESTROY_ROLES_VERSION_FIELD in effect
    if optional:
        from ..replacement.immutable import FrozenMap

        if FrozenMap(target_schema) != FrozenMap(optional_destroy_roles_schema()):
            return ()
        fields.add(OPTIONAL_DESTROY_ROLES_VERSION_FIELD)
    if (set(effect) != fields or not isinstance(groups,(list,tuple)) or not 2 <= len(groups) <= 4
            or effect.get('maximum_targets') != len(groups)):return ()
    try:required = set(compound_target_payload_dependencies(effect))
    except ValueError:return ()
    if COMPOUND_TARGET_SET_CAPABILITY not in required:return ()
    ids = set()
    for group in groups:
        if not isinstance(group,Mapping):return ()
        schema = dict(group);name = schema.pop('id',None)
        count = schema.pop('up_to', None) if optional else schema.get('count')
        if (type(name) is not str or not name or name in ids
                or type(count) is not int or count != 1):return ()
        ids.add(name);schema['count'] = 2
        prototype = {'op':effect['op'],'cards':'$targets','maximum_targets':2}
        caps = fixed_homogeneous_target_set_node_capabilities(effects=(prototype,),target_schema=schema,
            mechanic_ids=(*mechanic_ids,'fixed-homogeneous-target-set'))
        if not caps:return ()
        required.update(caps)
    return tuple(sorted(required))


def optional_destroy_roles_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    effects = tuple(program.effects)
    nodes = tuple(nested_effects((effects, getattr(program, 'handlers', ()))))
    schema = program.target_schema
    groups = schema.get('groups', ()) if isinstance(schema, Mapping) else ()
    optional = isinstance(groups, (list, tuple)) and any(
        isinstance(group, Mapping) and 'up_to' in group for group in groups
    )
    marked = any(OPTIONAL_DESTROY_ROLES_VERSION_FIELD in effect for effect in nodes)
    if not marked and not (optional and any(effect.get('target_roles') is True for effect in nodes)):
        return ()
    if len(effects) != 1 or OPTIONAL_DESTROY_ROLES_VERSION_FIELD not in effects[0]:
        raise ValueError('Optional destruction requires the explicit complete two-role instruction')
    required = compound_target_set_node_capabilities(
        effects=effects, target_schema=schema,
        mechanic_ids=(COMPOUND_TARGET_SET_MECHANIC, 'destroy', 'cr-115-targets'),
    )
    if not required:
        raise ValueError('Optional destruction requires the exact separate planeswalker and artifact roles')
    return required
