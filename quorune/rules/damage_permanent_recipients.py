"""Runtime admission independently validates the sealed damaged-creature role."""
from collections.abc import Mapping
from ..damage_recipient_model import DAMAGE_PERMANENT_CAPABILITY,DAMAGE_PERMANENT_OBJECT,uses_damaged_permanent
from ..delayed_object_model import delayed_object_node_capabilities
from ..creature_subtypes import canonical_creature_subtype


def _guarantees(condition,expected):
    if not isinstance(condition,Mapping):return False
    if condition==expected:return True
    if set(condition)=={'all'} and isinstance(condition['all'],(list,tuple)):
        return any(_guarantees(child,expected) for child in condition['all'])
    if set(condition)=={'any'} and isinstance(condition['any'],(list,tuple)) and condition['any']:
        return all(_guarantees(child,expected) for child in condition['any'])
    return False


def damage_permanent_runtime_dependencies(program):
    if not uses_damaged_permanent(program.effects):return ()
    condition=program.event_condition
    own_source=(_guarantees(condition,{'field':'source','op':'eq','value':'$source.ref'})
        and _guarantees(condition,{'field':'source_logical_object_id','op':'eq','value':'$source.logical_object_id'}))
    controlled_source=_guarantees(condition,{'field':'source_controller','op':'eq','value':'$source.controller'})
    def has_subtype(value):
        if not isinstance(value,Mapping):return False
        if (set(value)=={'field','op','value'} and value['field']=='source_subtypes' and value['op']=='contains_any'
                and isinstance(value['value'],(list,tuple)) and len(value['value'])==1
                and type(value['value'][0])is str and canonical_creature_subtype(value['value'][0])==value['value'][0]):return True
        if set(value)=={'all'} and isinstance(value['all'],(list,tuple)):return any(has_subtype(child) for child in value['all'])
        if set(value)=={'any'} and isinstance(value['any'],(list,tuple)) and value['any']:return all(has_subtype(child) for child in value['any'])
        return False
    controlled_source=controlled_source and (_guarantees(condition,{'field':'source_types','op':'contains_any','value':['creature']}) or has_subtype(condition))
    if (program.event!='damage.dealt' or program.active_zone!='battlefield' or program.target_schema is not None
            or not (own_source or controlled_source)
            or not _guarantees(condition,{'field':'target_kind','op':'eq','value':'permanent'})
            or not _guarantees(condition,{'field':'target_types','op':'contains_any','value':['creature']})):
        raise ValueError('Damaged-creature result requires its closed source and permanent-recipient event predicates')
    required=delayed_object_node_capabilities(effects=program.effects,target_schema=None,mechanic_ids=program.coverage)
    if not required or program.effects[0].get('object')!=DAMAGE_PERMANENT_OBJECT:
        raise ValueError('Damaged-creature result requires one independently closed delayed action')
    return tuple(sorted({DAMAGE_PERMANENT_CAPABILITY,'trigger.event.normalized_damage',*required}))
