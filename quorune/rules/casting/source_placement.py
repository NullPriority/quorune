"""Read-only mana affordability after the declared source leaves its origin."""
from dataclasses import dataclass
from ...cast_source_placement_model import cast_source_placement_version
from .public_conditions import prospective_cost_condition_view


@dataclass(frozen=True,slots=True)
class _ProspectiveCharacteristicReadHost:
    original: object
    state: object

    def __getattr__(self,name):
        value=getattr(self.original,name)
        if getattr(value,'__self__',None) is self.original and hasattr(value,'__func__'):
            return value.__func__.__get__(self,type(self))
        return value


@dataclass(frozen=True,slots=True)
class _CastAffordabilityHost:
    original: object
    source: object

    def __getattr__(self,name):return getattr(self.original,name)

    def _cost_is_affordable(self,seat,requirements,**kwargs):
        from ...cost_payments import cost_is_affordable
        return cost_is_affordable(prospective_characteristic_read_host(self.original,seat,self.source),seat,requirements,**kwargs)


def prospective_characteristic_read_host(host,seat,source):
    view,_spell=prospective_cost_condition_view(host,seat,source)
    return _ProspectiveCharacteristicReadHost(view.original,view.state)


def casting_affordability_host(host,source):
    if isinstance(host,_CastAffordabilityHost):host=host.original
    if (cast_source_placement_version(getattr(getattr(host,'state',None),'cast_source_placement_version',None))==1
            and source.zone!='stack'):
        return _CastAffordabilityHost(host,source)
    return host


def power_sacrifice_read_host(host, actor, source_object_id):
    """A numeric casting price reads the same post-placement state as payment."""
    if isinstance(host, _CastAffordabilityHost):host = host.original
    source = host.state.cards.get(source_object_id)
    if (source is not None and source.zone != 'stack'
            and cast_source_placement_version(getattr(host.state, 'cast_source_placement_version', None)) == 1):
        return prospective_characteristic_read_host(host, actor, source)
    return host
