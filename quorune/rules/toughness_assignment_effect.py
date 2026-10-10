from __future__ import annotations

"""One exact target rule committed by the existing duration journal owner."""

from ..toughness_assignment_model import TOUGHNESS_ASSIGNMENT_CAPABILITY,TEMPORARY_TOUGHNESS_MECHANIC
from ..toughness_assignment_rule import ResolvedToughnessAssignmentRule
from ..continuous_effect_model import ContinuousObjectIdentity
from ..continuous_effect_state import commit_continuous_effect,resolution_effect_source
from ..errors import GameRuleError


def validate_temporary_toughness_instruction(effect):
    if set(effect)-{'_runtime_source'}!={'op','schema_version','permission','card'} or effect['op']!='apply_source_characteristics_until_end_of_turn' or type(effect['schema_version']) is not int or effect['schema_version']!=5 or effect['permission']!='use_toughness' or type(effect['card']) is not str or not effect['card']:
        raise ValueError('Temporary toughness assignment instruction has a closed schema')


def temporary_toughness_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1 or TEMPORARY_TOUGHNESS_MECHANIC not in mechanic_ids:return ()
    try:validate_temporary_toughness_instruction(effects[0])
    except (TypeError,ValueError,KeyError):return ()
    from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed,direct_target_predicate_capabilities
    if effects[0]['card']!='$target.0' or not direct_permanent_target_schema_is_closed(target_schema) or target_schema.get('types_any',target_schema.get('types_all'))!=['creature']:return ()
    return tuple(sorted({TOUGHNESS_ASSIGNMENT_CAPABILITY,'target.revalidate_resolution',*direct_target_predicate_capabilities(target_schema)}))


def apply_temporary_toughness_assignment(host,effect,*,actor,reason):
    try:validate_temporary_toughness_instruction(effect)
    except (TypeError,ValueError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    try:card=host._resolve_object(actor,effect['card'],zones={'battlefield'})
    except GameRuleError:return ()
    if 'creature' not in host._type_parts(str(host._effective_card_data(card).get('type_line') or ''))[0]:return ()
    source=resolution_effect_source(host,effect,fallback_card=card)
    rule=ResolvedToughnessAssignmentRule(host._next_ref('TR'),source.object_id or source.stack_ref,host._next_zone_timestamp(),
        (ContinuousObjectIdentity(card.object_id,card.logical_object_id),))
    commit_continuous_effect(host.state,rule)
    host._log(actor,'combat.toughness_rule',f'{reason}: {card.ref} assigns combat damage using toughness this turn.',{'object':card.ref},importance=1,changed_objects=[card.object_id])
    return card.ref
