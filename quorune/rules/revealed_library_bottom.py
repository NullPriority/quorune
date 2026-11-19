"""Place only still-revealed identities using the canonical private library-order port."""
from dataclasses import dataclass
from collections.abc import Mapping
from ..library_order_model import LibraryArrivalOrder
from ..errors import GameRuleError
from .ripple_resolution_model import RippleBottomIntent


@dataclass(frozen=True,slots=True)
class RevealedLibraryBottomOrder(LibraryArrivalOrder):
    @property
    def origin_zones(self):
        return ('library',)

    def __post_init__(self):
        LibraryArrivalOrder.__post_init__(self)
        if self.position!='bottom':raise ValueError('Revealed remainder placement requires the bottom edge')

    def to_dict(self):
        return {**LibraryArrivalOrder.to_dict(self),'scope':'revealed_library_bottom_v1'}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'chooser','position','members','scope'} or value['scope']!='revealed_library_bottom_v1':
            raise ValueError('Revealed-library bottom order requires its explicit scope')
        return cls(value['chooser'],value['position'],value['members'])


def execute_ripple_bottom(host,intent):
    if not isinstance(intent,RippleBottomIntent):raise GameRuleError('Ripple bottom requires its typed intent')
    host._require_seat(intent.actor,in_game=True)
    item=next((row for row in host.state.stack if row.ref==intent.stack_ref),None)
    if item is None or item.controller!=intent.actor or not item.context.get('currently_resolving'):
        raise GameRuleError('Ripple bottom lost its original resolving trigger')
    from .ripple import RIPPLE_CAST_CONTEXT
    if item.context.get(RIPPLE_CAST_CONTEXT)!=intent.state.source.to_dict():
        raise GameRuleError('Ripple bottom source facts changed')
    library=host.state.players[intent.actor].zones['library'];cards={}
    for identity in intent.state.looked:
        card=host._resolve_object(intent.actor,identity.ref,zones={'library'},owned_only=True)
        if (card.object_id!=identity.object_id or card.logical_object_id!=identity.logical_object_id
                or card.owner!=intent.actor or card.object_id not in library):
            raise GameRuleError('A Ripple bottom card changed incarnation or owned library')
        cards[identity.ref]=card
    if intent.order:
        from .library_partition import commit_ordered_library_arrivals
        order=RevealedLibraryBottomOrder(intent.actor,'bottom',tuple(cards[ref].object_id for ref in intent.order))
        commit_ordered_library_arrivals(library,order,tuple(cards.values()))
    host._log(intent.actor,'library.revealed_remainder_bottom',
        f'{intent.actor} put {len(cards)} revealed card(s) on the bottom of their library.',
        {'count':len(cards),'objects':[identity.ref for identity in intent.state.looked]},
        visibility=None,importance=1,changed_objects=[c.object_id for c in cards.values()],
        changed_players=[intent.actor])
    return len(cards)
