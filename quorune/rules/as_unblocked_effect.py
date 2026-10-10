from __future__ import annotations

"""Separate temporary ability grants and accepted assignment rule effects."""

from ..as_unblocked import AS_UNBLOCKED_CAPABILITY,TEMPORARY_AS_UNBLOCKED_MECHANIC,AsUnblockedAssignmentPermission
from ..ability_fragments import ability_fragment_to_dict
from ..object_predicate import ObjectQuerySpec
from ..continuous_effects import ContinuousOperation,Layer
from ..continuous_effect_state import create_resolution_continuous_effect,matching_battlefield_objects,resolution_effect_source,commit_continuous_effect
from ..as_unblocked_rule import AsUnblockedAssignmentRule
from ..continuous_effect_model import ContinuousObjectIdentity
from ..errors import GameRuleError


def temporary_as_unblocked_selection(effect):
    required={'op','schema_version','permission','mode'}
    fields=set(effect)-{'_runtime_source'}
    if fields not in (required|{'card'},required|{'predicate'}):raise ValueError('Temporary as-unblocked grant has a closed schema')
    if effect['op']!='apply_source_characteristics_until_end_of_turn' or type(effect['schema_version']) is not int or effect['schema_version']!=4 or effect['permission']!='assign_as_unblocked':
        raise ValueError('Temporary as-unblocked grant requires its versioned permission')
    if effect['mode'] not in {'grant_ability','assignment_rule'}:raise ValueError('Temporary assignment mode is unsupported')
    return ObjectQuerySpec.from_dict(effect['predicate']) if 'predicate' in effect else effect['card']


def temporary_as_unblocked_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1 or target_schema is not None or TEMPORARY_AS_UNBLOCKED_MECHANIC not in mechanic_ids:return ()
    try:selection=temporary_as_unblocked_selection(effects[0])
    except (TypeError,ValueError,KeyError):return ()
    if isinstance(selection,ObjectQuerySpec):
        from ..compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
        if 'creature' not in selection.types_all or not fixed_resolution_characteristic_query_is_closed(selection,target_schema=None):return ()
        if effects[0]['mode']=='assignment_rule' and selection!=ObjectQuerySpec(zones=('battlefield',),controller='$controller',types_all=('creature',)):return ()
    elif selection!='$source.zone_object':return ()
    return (AS_UNBLOCKED_CAPABILITY,'continuous.resolution.fixed_characteristics_until_end_of_turn')


def is_closed_optional_as_unblocked_program(program):
    if TEMPORARY_AS_UNBLOCKED_MECHANIC not in program.coverage:return False
    from .fixed_effect_clause_shapes import fixed_optional_effect_node_capabilities
    required=set(fixed_optional_effect_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage))
    return bool(required) and required<=set(program.capability_dependencies)


def apply_temporary_as_unblocked(host,effect,*,actor,reason):
    try:selection=temporary_as_unblocked_selection(effect)
    except (TypeError,ValueError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    if effect['mode']=='assignment_rule':
        if isinstance(selection,ObjectQuerySpec):
            if selection!=ObjectQuerySpec(zones=('battlefield',),controller=actor,types_all=('creature',)):raise GameRuleError('As-unblocked rule supports only the resolving controller current creature set')
            affected=();controller=actor
        else:
            try:affected=(host._resolve_object(actor,selection,zones={'battlefield'}),)
            except GameRuleError:return ()
            controller=None
        source=resolution_effect_source(host,effect,fallback_card=affected[0] if affected else None)
        rule=AsUnblockedAssignmentRule(effect_id=host._next_ref('AR'),source_id=source.object_id or source.stack_ref,
            timestamp=host._next_zone_timestamp(),controller=controller,
            locked_objects=tuple(ContinuousObjectIdentity(card.object_id,card.logical_object_id) for card in affected))
        commit_continuous_effect(host.state,rule)
        host._log(actor,'combat.assignment_rule',f'{reason}: selected creatures assign damage as unblocked this turn.',{'controller':controller},importance=1)
        return ()
    if isinstance(selection,ObjectQuerySpec):affected=matching_battlefield_objects(host,selection)
    else:
        try:affected=(host._resolve_object(actor,selection,zones={'battlefield'}),)
        except GameRuleError:return ()
    create_resolution_continuous_effect(host,source=resolution_effect_source(host,effect,fallback_card=affected[0] if affected else None),
        targets=affected,layer=Layer.ABILITY,sublayer='6',
        operations=(ContinuousOperation('add_ability_fragment',ability_fragment_to_dict(AsUnblockedAssignmentPermission())),))
    refs=tuple(card.ref for card in affected)
    host._log(actor,'permanent.assignment_permission',f'{reason}: selected creatures may assign combat damage as unblocked.',
        {'objects':list(refs)},importance=1,changed_objects=[card.object_id for card in affected])
    return refs
