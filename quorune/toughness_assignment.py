from __future__ import annotations

"""Read current external assignment rules only at the combat boundary."""

from .toughness_assignment_model import ToughnessAssignmentSpec
from .toughness_assignment_rule import active_resolved_toughness_rule
from .object_query import object_query_result,object_matches_query


def current_toughness_rule_sources(host):
    return tuple((source,rule) for source in host._semantic_event_sources(zones={'battlefield'})
        if source.zone=='battlefield' and not source.phased_out
        for rule in host._effective_ability_fragments(source) if isinstance(rule,ToughnessAssignmentSpec))


def current_toughness_assignment(host,card,*,power,toughness,rule_sources=None):
    if card.zone!='battlefield' or card.phased_out:return False
    if active_resolved_toughness_rule(host.state,card):return True
    row=None
    for source,rule in current_toughness_rule_sources(host) if rule_sources is None else rule_sources:
        if rule.during_controller_turn and host.state.active_player!=source.controller:continue
        if rule.scope=='self' and source.logical_object_id!=card.logical_object_id:continue
        if rule.scope=='attached' and source.attached_to!=card.object_id:continue
        if rule.scope=='controller' and source.controller!=card.controller:continue
        if rule.toughness_greater_than_power and toughness<=power:continue
        if row is None:
            data=host._effective_card_data(card)
            row=object_query_result(card,data,type_parts=host._type_parts(str(data.get('type_line') or '')),known_to_actor=True,attached_to_ref=None)
        if object_matches_query(row,rule.predicate):return True
    return False
