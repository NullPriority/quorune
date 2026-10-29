"""Sealed Ring emblem occurrences and inherent trigger stack construction."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Protocol

from .model import StackItem
from .object_predicate import ObjectQuerySpec
from .ring_model import RingObjectIdentity, RingRuleView, RingStateError
from .target_source import TARGETING_SOURCE_CONTEXT, TargetingSourceBinding


class RingTriggerKind(str, Enum):
    ATTACK = 'attack'
    BLOCKED_BY_CREATURE = 'blocked_by_creature'
    COMBAT_DAMAGE_TO_PLAYER = 'combat_damage_to_player'


@dataclass(frozen=True, slots=True)
class RingCombatOccurrence:
    """Facts captured at occurrence time, not reconstructed at resolution."""
    kind: RingTriggerKind
    bearer: RingObjectIdentity
    bearer_controller: str
    blocker: RingObjectIdentity | None = None
    damaged_player: str | None = None
    dealt_amount: int = 0
    combat: bool = False

    def __post_init__(self):
        if not isinstance(self.kind,RingTriggerKind) or not isinstance(self.bearer,RingObjectIdentity):
            raise RingStateError('Ring triggers require a typed occurrence and bearer incarnation')
        if type(self.bearer_controller) is not str or not self.bearer_controller:
            raise RingStateError('Ring occurrence controller must be known')
        if type(self.dealt_amount) is not int or self.dealt_amount < 0 or type(self.combat) is not bool:
            raise RingStateError('Ring damage facts must be exact committed values')
        if self.kind is RingTriggerKind.BLOCKED_BY_CREATURE:
            if not isinstance(self.blocker,RingObjectIdentity) or self.blocker.object_id==self.bearer.object_id:
                raise RingStateError('A blocked-by occurrence requires a distinct blocker incarnation')
        elif self.blocker is not None:
            raise RingStateError('Only a blocked-by occurrence names a blocker')
        if self.kind is RingTriggerKind.COMBAT_DAMAGE_TO_PLAYER:
            if type(self.damaged_player) is not str or not self.damaged_player:
                raise RingStateError('Ring damage requires its damaged player')
        elif self.damaged_player is not None or self.dealt_amount or self.combat:
            raise RingStateError('Only a damage occurrence carries damage facts')


@dataclass(frozen=True, slots=True)
class RingEmblemTrigger:
    source: RingRuleView
    occurrence: RingCombatOccurrence

    def __post_init__(self):
        if not isinstance(self.source,RingRuleView) or not isinstance(self.occurrence,RingCombatOccurrence):
            raise RingStateError('Ring trigger requires sealed source and occurrence facts')
        bearer = self.source.state.bearer
        if (bearer is None or bearer.identity != self.occurrence.bearer
                or self.source.state.player != self.occurrence.bearer_controller):
            raise RingStateError('Ring trigger occurrence does not match its designated bearer')
        required = {RingTriggerKind.ATTACK:2,RingTriggerKind.BLOCKED_BY_CREATURE:3,
                    RingTriggerKind.COMBAT_DAMAGE_TO_PLAYER:4}[self.occurrence.kind]
        if self.source.state.ability_level < required:
            raise RingStateError('The emblem has not gained this triggered ability')
        if self.occurrence.kind is RingTriggerKind.COMBAT_DAMAGE_TO_PLAYER and (
            not self.occurrence.combat or self.occurrence.dealt_amount == 0
        ):
            raise RingStateError('The Ring requires actual combat damage to a player')

    def effects(self) -> tuple[dict[str,Any], ...]:
        if self.occurrence.kind is RingTriggerKind.ATTACK:
            return (
                {'op':'draw','player':'$controller','count':1,'private':True},
                {'op':'choose_cards_apnap','actor':'$controller','players':['$controller'],
                 'zone':'hand','predicate':ObjectQuerySpec(zones=('hand',)).to_dict(),
                 'count':1,'then':'discard','hidden':True,'prompt':'Discard a card.'},
            )
        if self.occurrence.kind is RingTriggerKind.COMBAT_DAMAGE_TO_PLAYER:
            return ({'op':'lose_life_each_opponent','amount':3},)
        emblem = self.source.state.emblem
        binding = TargetingSourceBinding(emblem.object_id,emblem.object_ref,emblem.logical_object_id,
                                         'command',self.source.state.player,'',())
        return ({'op':'delayed_trigger','controller':'$controller',
                 'label':'The Ring — sacrifice blocking creature','event':'step.begin',
                 'condition':{'phase':'combat','step':'end_combat'},
                 'source_binding':binding.to_dict(),
                 'referred_object_ids':[self.occurrence.blocker.object_id],
                 'stack':{'label':'The Ring — sacrifice blocking creature','context':{
                     TARGETING_SOURCE_CONTEXT:binding.to_dict(),
                     'source_logical_object_id':emblem.logical_object_id,
                     'dynamic_effects':[{'op':'sacrifice_if_present',
                        'card':self.occurrence.blocker.object_ref,
                        'object_identity':self.occurrence.blocker.to_dict(),
                        'current_controller_sacrifices':True}],
                 }}},)


def ring_trigger_specs(
    views: Iterable[RingRuleView], occurrence: RingCombatOccurrence,
) -> tuple[RingEmblemTrigger,...]:
    if not isinstance(occurrence,RingCombatOccurrence):
        raise RingStateError('Ring trigger discovery requires a sealed occurrence')
    required = {RingTriggerKind.ATTACK:2,RingTriggerKind.BLOCKED_BY_CREATURE:3,
                RingTriggerKind.COMBAT_DAMAGE_TO_PLAYER:4}[occurrence.kind]
    result = []
    for view in views:
        if not isinstance(view,RingRuleView):
            raise RingStateError('Ring trigger discovery requires typed source facts')
        bearer = view.state.bearer
        if (bearer is None or bearer.identity != occurrence.bearer
                or view.state.player != occurrence.bearer_controller
                or view.state.ability_level < required):
            continue
        if occurrence.kind is RingTriggerKind.COMBAT_DAMAGE_TO_PLAYER and (
            not occurrence.combat or occurrence.dealt_amount == 0
        ):
            continue
        result.append(RingEmblemTrigger(view,occurrence))
    return tuple(result)


class RingTriggerHost(Protocol):
    seats: list[str]
    def _next_ref(self, prefix: str) -> str: ...
    def _stable_runtime_id(self, kind: str, ref: str) -> str: ...


def materialize_ring_trigger(host: RingTriggerHost, trigger: RingEmblemTrigger) -> StackItem:
    if not isinstance(trigger,RingEmblemTrigger):
        raise RingStateError('Ring stack creation requires a typed inherent trigger')
    state = trigger.source.state
    source = state.emblem
    ref = host._next_ref('S')
    binding = TargetingSourceBinding(source.object_id,source.object_ref,source.logical_object_id,
                                    'command',state.player,'',())
    return StackItem(
        stack_id=host._stable_runtime_id('stack',ref),ref=ref,kind='triggered_ability',
        controller=state.player,label='The Ring — '+trigger.occurrence.kind.value.replace('_',' '),
        source_object_id=source.object_id,visibility=list(host.seats),
        context={'source_logical_object_id':source.logical_object_id,
                 TARGETING_SOURCE_CONTEXT:binding.to_dict(),
                 'ring_trigger_kind':trigger.occurrence.kind.value,
                 'ring_bearer_object_id':trigger.occurrence.bearer.object_id,
                 'ring_bearer_logical_object_id':trigger.occurrence.bearer.logical_object_id,
                 'dynamic_effects':list(trigger.effects())},
        referred_object_ids=([trigger.occurrence.blocker.object_id]
                             if trigger.occurrence.blocker is not None else []))


def _occurrence_stack_items(host, views, occurrences, *, event):
    """Use the emblem, rather than its bearer, for multiplier participation."""
    from .trigger_discovery import trigger_multiplier_copies
    result = []
    for occurrence in occurrences:
        for trigger in ring_trigger_specs(views, occurrence):
            source = host.state.cards[trigger.source.state.emblem.object_id]
            item = materialize_ring_trigger(host, trigger)
            context = {
                'card': occurrence.bearer.object_ref,
                'controller': occurrence.bearer_controller,
                'types': ['creature'],
            }
            item.context.update({'event': event, 'event_context': context})
            result.append(item)
            result.extend(trigger_multiplier_copies(
                host, item=item, source=source, event=event, context=context))
    return tuple(result)


def ring_attack_stack_items(host, event):
    """Capture inherent triggers from the canonical sealed attack transition."""
    from .attack_transition_model import AttackTransitionEvent
    from .ring_state import current_ring_rule_views
    if not isinstance(event, AttackTransitionEvent):
        raise RingStateError('Ring attacks require a sealed attack transition')
    views = current_ring_rule_views(host)
    if not views:
        return ()
    participants = {value.object_id: value for value in event.participants}
    occurrences = []
    for assignment in event.assignments:
        bearer = participants[assignment.attacker_object_id]
        occurrences.append(RingCombatOccurrence(
            RingTriggerKind.ATTACK,
            RingObjectIdentity(bearer.object_id, bearer.reference, bearer.logical_object_id),
            bearer.controller))
    return _occurrence_stack_items(host, views, occurrences, event='creature.attacks')


def ring_block_stack_items(host, event):
    """Capture one trigger for each new sealed blocking relationship."""
    from .block_transitions import BlockTransitionEvent
    from .ring_state import current_ring_rule_views
    if not isinstance(event, BlockTransitionEvent):
        raise RingStateError('Ring blocks require a sealed block transition')
    views = current_ring_rule_views(host)
    if not views:
        return ()
    participants = {value.object_id: value for value in event.participants}
    occurrences = []
    for assignment in event.assignments:
        bearer = participants[assignment.attacker_object_id]
        blocker = participants[assignment.blocker_object_id]
        occurrences.append(RingCombatOccurrence(
            RingTriggerKind.BLOCKED_BY_CREATURE,
            RingObjectIdentity(bearer.object_id, bearer.reference, bearer.logical_object_id),
            bearer.controller,
            blocker=RingObjectIdentity(blocker.object_id, blocker.reference, blocker.logical_object_id)))
    return _occurrence_stack_items(host, views, occurrences, event='creature.becomes_blocked_by')


def ring_damage_stack_items(host, events):
    """Seal damage triggers before damage and its aftermath mutate the world."""
    from .ring_state import current_ring_rule_views
    views = current_ring_rule_views(host)
    if not views:
        return ()
    bearer_ids = {view.state.bearer.identity.object_id for view in views
                  if view.state.bearer is not None}
    occurrences = []
    for event in events:
        if not (event.was_dealt and event.combat and event.target_kind == 'player'
                and event.source_object_id in bearer_ids):
            continue
        occurrences.append(RingCombatOccurrence(
            RingTriggerKind.COMBAT_DAMAGE_TO_PLAYER,
            RingObjectIdentity(event.source_object_id, event.source, event.source_logical_object_id),
            event.source_controller, damaged_player=event.target,
            dealt_amount=event.dealt_amount, combat=True))
    return _occurrence_stack_items(host, views, occurrences, event='damage.dealt')
