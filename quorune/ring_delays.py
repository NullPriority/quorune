"""Owned CR 701.54c blocker delay over canonical trigger and zone owners."""
from __future__ import annotations

from typing import Any, Protocol

from .ring_model import RingObjectIdentity, RingStateError
from .target_source import TARGETING_SOURCE_CONTEXT, TargetingSourceBinding
from .trigger_processing import schedule_delayed_trigger


class RingDelayHost(Protocol):
    state: Any
    active_seats: list[str]

    def apply_effect(self, effect, *, actor: str): ...


def schedule_ring_blocker_sacrifice(
    host: RingDelayHost, *, controller: str, emblem: RingObjectIdentity,
    emblem_owner: str, blocker: RingObjectIdentity, reason: str,
):
    if type(controller) is not str or controller not in host.active_seats:
        raise RingStateError('A Ring delay requires its current ability controller')
    if (not isinstance(emblem,RingObjectIdentity) or not isinstance(blocker,RingObjectIdentity)
            or type(emblem_owner) is not str or not emblem_owner
            or type(reason) is not str or not reason.strip()):
        raise RingStateError('Ring delay requires sealed source, blocker and provenance')
    source = host.state.cards.get(emblem.object_id)
    if source is not None and (
        source.object_kind != 'emblem' or source.zone != 'command'
        or source.ref != emblem.object_ref or source.logical_object_id != emblem.logical_object_id
        or source.owner != emblem_owner or source.controller != emblem_owner
    ):
        raise RingStateError('Ring delay source conflicts with its captured emblem')
    binding = TargetingSourceBinding(emblem.object_id,emblem.object_ref,emblem.logical_object_id,
                                    'command',emblem_owner,'',())
    return schedule_delayed_trigger(
        host,controller=controller,label='The Ring — sacrifice blocking creature',
        event_kind='step.begin',condition={'phase':'combat','step':'end_combat'},
        source_object_id=emblem.object_id,source_logical_object_id=emblem.logical_object_id,
        referred_object_ids=(blocker.object_id,),
        # No "this turn" duration: a skipped end-combat step does not expire it.
        stack_template={
            'label':'The Ring — sacrifice blocking creature',
            'context':{
                TARGETING_SOURCE_CONTEXT:binding.to_dict(),
                'source_logical_object_id':emblem.logical_object_id,
                'dynamic_effects':[{'op':'sacrifice_if_present','card':blocker.object_ref,
                                    'object_identity':blocker.to_dict(),
                                    'current_controller_sacrifices':True}],
            },
        },
    )


def resolve_ring_blocker_sacrifice(
    host: RingDelayHost, *, blocker: RingObjectIdentity, reason: str,
) -> tuple[str,str] | None:
    """The same blocker is sacrificed by its controller when this resolves."""
    if not isinstance(blocker,RingObjectIdentity) or type(reason) is not str or not reason.strip():
        raise RingStateError('Ring sacrifice requires its sealed blocker and provenance')
    card = host.state.cards.get(blocker.object_id)
    if (card is None or card.zone != 'battlefield' or card.ref != blocker.object_ref
            or card.logical_object_id != blocker.logical_object_id):
        return None
    if card.controller not in host.active_seats:
        raise RingStateError('The blocking creature has no active current controller')
    controller = card.controller
    # Use the canonical sacrifice effect/zone transition, not a direct removal.
    host.apply_effect({'op':'sacrifice','card':card.ref,'reason':reason},actor=controller)
    return controller,card.ref
