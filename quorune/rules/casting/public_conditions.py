"""Read-only prospective spell-state facts for existing public cost predicates."""
from dataclasses import dataclass,replace
from collections import ChainMap
from collections.abc import Mapping
from types import MappingProxyType
from functools import partial

PUBLIC_SELF_COST_CAPABILITY='casting.cost.self_public_state_condition'


@dataclass(frozen=True,slots=True)
class _StateView:
    original:object
    cards:Mapping
    players:Mapping
    def __getattr__(self,name):return getattr(self.original,name)


@dataclass(frozen=True,slots=True)
class _HostView:
    original:object
    state:object
    def __getattr__(self,name):return getattr(self.original,name)


def prospective_cost_condition_view(host,seat,source):
    """Read the candidate as a spell after601.2a, without mutating the game."""
    from ...self_cast_reductions import SelfCastReductionError
    from ...model import CardInstance
    if not isinstance(source,CardInstance) or seat not in host.state.players:
        raise SelfCastReductionError('Shared self cost condition requires a current casting source and seat')
    actual=host.state.cards.get(source.object_id)
    if actual is None or actual.ref!=source.ref:
        raise SelfCastReductionError('Shared self cost source is unavailable or replaced')
    spell=replace(source,owner=actual.owner,controller=seat,zone='stack')
    players={}
    for player_seat,player in host.state.players.items():
        if actual.zone=='stack':players[player_seat]=player;continue
        zones={zone:[oid for oid in ids if oid!=source.object_id] for zone,ids in player.zones.items()}
        if player_seat==actual.owner and 'stack' in zones:zones['stack'].append(source.object_id)
        players[player_seat]=replace(player,zones=zones)
    state=_StateView(host.state,ChainMap(MappingProxyType({source.object_id:spell}),host.state.cards),MappingProxyType(players))
    return _HostView(host,state),spell


def self_public_condition_multiplier(host,seat,source,condition):
    from ...card_programs.runtime import fixed_public_state_condition_matches
    from ...dynamic_characteristics import query_characteristic_count
    from ...self_cast_reductions import SelfCastReductionError
    view,spell=prospective_cost_condition_view(host,seat,source)
    def public_object(card):
        current=spell if card.object_id==spell.object_id else card
        return host._public_object_query_result(current,_maximum_layer=None)
    try:
        return int(fixed_public_state_condition_matches(view.state,spell,condition,
            public_object_resolver=public_object,
            quantity_resolver=partial(query_characteristic_count,view,_maximum_layer=None),require_available=True))
    except (AttributeError,KeyError,TypeError,ValueError) as exc:
        raise SelfCastReductionError('Shared self cost public facts are unavailable') from exc


def self_public_condition_dependencies(value):
    from ...self_cast_reductions import CastReductionMetric,CastReductionMetricKind
    from ...object_predicate import object_query_capabilities
    from ...characteristic_fragments import characteristic_quantity_capabilities
    from ...public_history_query_model import public_history_query_dependencies
    from ...public_zone_condition_model import public_zone_condition_capabilities
    from ...replacement.immutable import thaw_value
    if isinstance(value,Mapping):
        own=set()
        if value.get('kind')==CastReductionMetricKind.PUBLIC_STATE.value or ('public_state' in value and 'queries' in value):
            metric=CastReductionMetric.from_dict(thaw_value(value))
            if metric.kind is not CastReductionMetricKind.PUBLIC_STATE:raise ValueError('Shared cost condition has no typed metric owner')
            raw=metric.public_state.to_dict()
            own.update((PUBLIC_SELF_COST_CAPABILITY,*object_query_capabilities(raw),*characteristic_quantity_capabilities(raw),*public_history_query_dependencies(raw),*public_zone_condition_capabilities(raw)))
        return tuple(sorted(own|{cap for child in value.values() for cap in self_public_condition_dependencies(child)}))
    if isinstance(value,(list,tuple)):return tuple(sorted({cap for child in value for cap in self_public_condition_dependencies(child)}))
    return ()
