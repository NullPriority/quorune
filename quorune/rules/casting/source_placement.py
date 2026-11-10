"""Casting source authority, prospective prices, and immediate triggers."""
from collections import ChainMap
from dataclasses import dataclass, replace
from types import MappingProxyType
from ...cast_source_placement_model import cast_source_placement_version
from .public_conditions import prospective_cost_condition_view
from ...characteristic_read_frames import CharacteristicReadHost as _ProspectiveCharacteristicReadHost
from ...characteristic_read_frames import CharacteristicStateView


def revalidate_cast_source(host, proposal, details, response):
    """Resolve the exact declared source and recheck any live library access."""
    from .model import CastProposalError

    card = host._resolve_object(proposal.seat, proposal.card_ref, zones={proposal.origin}, owned_only=False)
    if card.object_id != proposal.object_id or card.zone != proposal.origin:
        raise CastProposalError('The casting source changed after proposal construction', reason='stale_source')
    record = host.card_record(card)
    if record is None:
        raise CastProposalError('The casting card record is unavailable')
    if card.zone == 'library':
        from ...morph import FACE_DOWN_CAST_METHODS
        from .face_queries import selected_cast_card_view, validate_library_top_cast_commit

        method = details.get('cast_method')
        view = selected_cast_card_view(host, card, proposal.face, method)
        try:
            validate_library_top_cast_commit(host, proposal.seat, view, details['selected_cost_option'], response,
                face_down=method in FACE_DOWN_CAST_METHODS)
        except ValueError as exc:
            raise CastProposalError(str(exc), reason='stale_library_top_permission') from exc
    return card, record, host.semantics.get(proposal.semantic_key)


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


def cast_payment_read_host(host, caster):
    """Read each payer in the prospective stack view of the actual caster."""
    if isinstance(host, _CastAffordabilityHost):
        return prospective_characteristic_read_host(host.original, caster, host.source)
    return host


def prospective_sacrifice_read_host(host, actor, source_object_id):
    """A declared sacrifice price reads the post-placement payment state."""
    if isinstance(host, _CastAffordabilityHost):host = host.original
    source = host.state.cards.get(source_object_id)
    if (source is not None and source.zone != 'stack'
            and cast_source_placement_version(getattr(host.state, 'cast_source_placement_version', None)) == 1):
        return prospective_characteristic_read_host(host, actor, source)
    return host


def prospective_fixed_tap_price_host(host, seat, source_object_id, tapped):
    """Read a later zone price after the reserved tap, without changing game state."""
    base = prospective_sacrifice_read_host(host, seat, source_object_id)
    original = base.original if isinstance(base, _ProspectiveCharacteristicReadHost) else base
    changes = {card.object_id: replace(base.state.cards[card.object_id], tapped=True) for card in tapped}
    state = CharacteristicStateView(base.state, ChainMap(MappingProxyType(changes), base.state.cards),
        base.state.players)
    return _ProspectiveCharacteristicReadHost(original, state)


def power_sacrifice_read_host(host, actor, source_object_id):
    return prospective_sacrifice_read_host(host, actor, source_object_id)


def observe_cast_source_placement(host, item) -> None:
    from ..state_triggers import observe_public_state_triggers
    from ..target_announcements import announce_initial_target_triggers

    observe_public_state_triggers(host)
    announce_initial_target_triggers(host, item)
