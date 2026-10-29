"""Closed reductions over typed current-turn history, never presentation logs."""

from dataclasses import dataclass
from collections.abc import Mapping

PUBLIC_HISTORY_QUERY_CAPABILITY='condition.history.fixed_public_query'
SPELL_ORIGIN_HISTORY_CAPABILITY='condition.history.spell_origin'
GRAVEYARD_TYPE_HISTORY_CAPABILITY='condition.history.graveyard_card_types'
_EVENTS=frozenset({'spell_cast','creature_attacked','creature_died','permanent_sacrificed',
    'player_gained_life','player_lost_life','permanent_entered','permanent_left','card_arrived_graveyard'})
_TYPES=frozenset({'artifact','battle','creature','enchantment','instant','kindred','land','planeswalker','sorcery'})


@dataclass(frozen=True,slots=True)
class PublicHistoryQuerySpec:
    event:str
    relation:str='controller'
    types_all:tuple[str,...]=()
    types_any:tuple[str,...]=()
    excluded_types:tuple[str,...]=()
    reduction:str='count'
    schema_version:int=1
    origin:str|None=None

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in {1,2}:raise ValueError('Public history query version must be1 or2')
        if self.event not in _EVENTS or self.relation not in {'controller','any_player','opponents'}:raise ValueError('Unknown history event or principal relation')
        if self.reduction not in {'count','sum_amount','max_player_amount'}:raise ValueError('Unknown history reduction')
        life=self.event in {'player_gained_life','player_lost_life'}
        if life!=(self.reduction!='count'):raise ValueError('Only life history carries amount reductions')
        if self.reduction=='max_player_amount' and self.relation=='controller':raise ValueError('Per-player maximum requires a player set')
        for field in ('types_all','types_any','excluded_types'):
            values=getattr(self,field)
            if not isinstance(values,(tuple,list)) or any(type(v) is not str or v not in _TYPES for v in values) or len(values)!=len(set(values)):
                raise ValueError('History type predicates require distinct canonical card types')
            object.__setattr__(self,field,tuple(sorted(values)))
        typed_arrival=self.event=='card_arrived_graveyard' and any((self.types_all,self.types_any,self.excluded_types))
        if life and any((self.types_all,self.types_any,self.excluded_types)):
            raise ValueError('This history event does not seal type predicates')
        if self.event=='creature_attacked' and any((self.types_all,self.types_any,self.excluded_types)):
            raise ValueError('Attack history mixes type/subtype terms and has no general type filter')
        if set(self.types_all)&set(self.excluded_types):raise ValueError('History query has contradictory type predicates')
        if self.origin is not None:
            origins={'hand','library','graveyard','exile','command','outside'} if self.event=='spell_cast' else {
                'battlefield','hand','library','stack','exile','command','outside'} if self.event=='card_arrived_graveyard' else set()
            if type(self.origin) is not str or self.origin not in origins:raise ValueError('History origin requires one closed cast or arrival zone')
        if (self.schema_version==2)!=(typed_arrival or self.origin is not None):
            raise ValueError('Qualified history queries require version2; version1 keeps its original fields')

    def to_dict(self):
        return {'schema_version':self.schema_version,'event':self.event,'relation':self.relation,'types_all':list(self.types_all),
            'types_any':list(self.types_any),'excluded_types':list(self.excluded_types),'reduction':self.reduction,
            **({'origin':self.origin} if self.schema_version==2 else {})}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','event','relation','types_all','types_any','excluded_types','reduction'}|(
                {'origin'} if value.get('schema_version')==2 else set()):
            raise ValueError('Public history queries have a closed field set')
        return cls(**dict(value))

    @property
    def capability_dependencies(self):
        return (PUBLIC_HISTORY_QUERY_CAPABILITY,
            *((SPELL_ORIGIN_HISTORY_CAPABILITY,) if self.event=='spell_cast' and self.origin is not None else ()),
            *((GRAVEYARD_TYPE_HISTORY_CAPABILITY,) if self.event=='card_arrived_graveyard' and any((self.types_all,self.types_any,self.excluded_types)) else ()))


def matching_public_history_events(state,controller,spec):
    """Return sealed matching occurrences, or unavailable history."""
    history=getattr(state,'turn_history',None)
    if history is None or history.schema_version!=1 or history.turn_sequence!=state.turn_sequence:return None
    if spec.event=='permanent_left' and history.departure_history_version!=1:return None
    if spec.event=='card_arrived_graveyard' and history.arrival_history_version!=1:return None
    if spec.event=='spell_cast' and spec.origin is not None and history.spell_origin_history_version!=1:return None
    if GRAVEYARD_TYPE_HISTORY_CAPABILITY in spec.capability_dependencies and history.graveyard_type_history_version!=1:return None
    seats={seat for seat,player in state.players.items() if player.in_game}
    allowed={controller} if spec.relation=='controller' else seats-{controller} if spec.relation=='opponents' else set(state.players)
    matched=[]
    for event in history.events:
        if event.kind!=spec.event:continue
        seat=event.target if spec.event in {'player_gained_life','player_lost_life'} else event.actor
        if seat not in allowed:continue
        if spec.origin is not None:
            if event.origin is None:return None
            if event.origin!=spec.origin:continue
        types=set(event.types)
        if not set(spec.types_all)<=types or spec.types_any and types.isdisjoint(spec.types_any) or not types.isdisjoint(spec.excluded_types):continue
        matched.append(event)
    return tuple(matched)


def public_history_query_value(state,controller,spec):
    events=matching_public_history_events(state,controller,spec)
    if events is None:return None
    if spec.reduction=='count':return len(events)
    if spec.reduction=='sum_amount':return sum(event.amount for event in events)
    totals={}
    for event in events:
        seat=event.target if spec.event in {'player_gained_life','player_lost_life'} else event.actor
        totals[seat]=totals.get(seat,0)+event.amount
    return max(totals.values(),default=0)


def public_history_query_dependencies(value):
    required=set()
    def visit(node):
        if isinstance(node,Mapping):
            if 'history_query' in node or node.get('kind') in {'history_count_at_least','history_count_at_most'}:
                if (set(node)!={'schema_version','kind','amount','counter_name','history_query'} or type(node['schema_version']) is not int
                        or node['schema_version']!=8 or node['kind'] not in {'history_count_at_least','history_count_at_most'}
                        or node['counter_name'] is not None or type(node['amount']) is not int or node['amount']<0):
                    raise ValueError('History conditions have one closed nonnegative threshold')
                required.update(PublicHistoryQuerySpec.from_dict(node['history_query']).capability_dependencies)
            for child in node.values():visit(child)
        elif isinstance(node,(tuple,list)):
            for child in node:visit(child)
    visit(value)
    return tuple(sorted(required))
