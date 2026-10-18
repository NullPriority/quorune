from __future__ import annotations

"""Closed eligibility facts for conditional intrinsic entry copies."""

from collections.abc import Mapping
from dataclasses import dataclass

ENTRY_COPY_PREDICATE_HANDLER='replacement.zone.entry-copy.v4'
ENTRY_COPY_PREDICATE_CAPABILITY='zone.entry.copy_source_predicates'


@dataclass(frozen=True,slots=True)
class EntryCopyPredicate:
    kind: str
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1 or type(self.kind) is not str or self.kind not in {
            'controller_attacked_this_turn','graveyard_from_battlefield_this_turn','mana_value_at_most_mana_spent'}:
            raise ValueError('Entry copy predicate kind/version is closed')

    def to_dict(self):return {'schema_version':1,'kind':self.kind}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','kind'}:raise ValueError('Entry copy predicate fields are closed')
        return cls(value['kind'],value['schema_version'])


def actual_entry_mana_spent(host,card):
    items=[item for item in host.state.stack if item.kind in {'spell','spell_copy'} and item.card_object_id==card.object_id]
    if card.zone=='stack' and len(items)!=1:raise ValueError('Entry payment provenance requires one current stack object')
    if not items or items[0].kind!='spell':return 0
    value=items[0].context.get('mana_spent_total')
    if type(value) is not int or value<0:raise ValueError('Entry actual mana-spent provenance is missing or malformed')
    return value


def entry_copy_predicate_matches(host,predicate,*,entering,target,controller):
    from .turn_history import current_turn_history_events
    history=host.state.turn_history;turn=host.state.turn_sequence
    if predicate.kind=='controller_attacked_this_turn':
        return any(event.actor==controller for event in current_turn_history_events(history,turn_sequence=turn,kind='creature_attacked'))
    if predicate.kind=='graveyard_from_battlefield_this_turn':
        if history is None or history.arrival_history_version!=1:return False
        return target.zone=='graveyard' and target.is_card_object and any(
            event.object_incarnation==target.logical_object_id and event.origin=='battlefield'
            for event in current_turn_history_events(history,turn_sequence=turn,kind='card_arrived_graveyard'))
    value=host._effective_card_data(target).get('mana_value')
    if type(value) not in (int,float) or value<0:raise ValueError('Copy candidate mana value is unavailable')
    return value<=actual_entry_mana_spent(host,entering)
