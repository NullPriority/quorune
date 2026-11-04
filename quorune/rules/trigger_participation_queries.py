from __future__ import annotations

"""Read immutable event/source facts through the shared object-query owner."""

from ..object_query import ObjectQueryResult,object_matches_query
from ..trigger_participation_query_model import trigger_participation_rule


def _facts(source,characteristics):
    from ..zone_trigger_events import sealed_public_characteristic_facts
    facts=sealed_public_characteristic_facts(characteristics)
    return ObjectQueryResult(source.object_id,source.ref,source.printed_name,source.owner,source.controller,source.zone,
        types=tuple(facts['types']),subtypes=tuple(facts['subtypes']),supertypes=tuple(facts['supertypes']),
        colors=tuple(facts['colors']),keywords=tuple(facts['keywords']),logical_object_id=source.logical_object_id,
        attached_to_ref=source.attached_to)


def _event_facts(event,context):
    damage=event=='damage.dealt'
    prefix='source_' if damage else ''
    controller=context.get('source_controller' if damage else 'controller')
    kinds=context.get(prefix+'types',())
    if event=='creature.attacks' and not kinds:kinds=('creature',)
    return ObjectQueryResult('event','event','event',context.get(prefix+'owner') or controller or '',controller or '',
        context.get('source_zone' if damage else 'to', 'battlefield'),types=tuple(kinds),
        subtypes=tuple(context.get(prefix+'subtypes',())),supertypes=tuple(context.get(prefix+'supertypes',())),
        colors=tuple(context.get(prefix+'colors',())))


def participation_rule_matches(spec,modifier,modifier_characteristics,ability_source,source_characteristics,event,context,active_zone):
    rule=trigger_participation_rule(spec.rule,spec.subtype)
    if active_zone not in rule.source_zones:return False
    if rule.events and event.removesuffix('.self') not in rule.events:return False
    if rule.source_relation=='you' and ability_source.controller!=modifier.controller:return False
    if rule.source_relation=='opponent' and ability_source.controller==modifier.controller:return False
    source=_facts(ability_source,source_characteristics)
    if rule.source_queries and not any(object_matches_query(source,q) for q in rule.source_queries):return False
    if rule.modifier_condition=='equipped':
        if not modifier_characteristics.get('_trigger_participation_equipped'):return False
    same=modifier.object_id==ability_source.object_id
    if rule.exclusion=='modifier' and same:return False
    if rule.exclusion=='wizard_only' and same and 'shaman' not in source.subtypes:return False
    if rule.exclusion=='permanent_modifier' and same and active_zone=='battlefield':return False
    if rule.exclusion=='attached_source' and not same:
        if 'equipment' not in source.subtypes or ability_source.attached_to!=modifier.ref:return False
    event_row=_event_facts(event,context)
    departure=event.removesuffix('.self') in {'permanent.leave','creature.dies','artifact.graveyard','permanent.graveyard'}
    if spec.rule in {'creature_dies','prevent_creature_enters_dies'} and departure:
        if event.removesuffix('.self')!='creature.dies' and context.get('to')!='graveyard':return False
    if rule.event_relation=='you' and event_row.controller!=modifier.controller:return False
    if rule.event_queries and not any(object_matches_query(event_row,q) for q in rule.event_queries):return False
    if spec.rule=='controlled_combat_damage_player':
        return bool(context.get('combat') is True and context.get('target_kind')=='player'
            and 'creature' in event_row.types and context.get('amount',0)>0)
    return True
