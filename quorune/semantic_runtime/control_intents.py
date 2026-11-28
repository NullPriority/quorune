from __future__ import annotations

"""Immutable requests to the shared resolution-control owner."""

from dataclasses import dataclass,field

from ..continuous_effect_model import ContinuousEffectDuration, ContinuousObjectIdentity
from ..continuous_effect_state import ResolutionEffectSource
from ..object_predicate import ObjectQuerySpec
from ..owner_control_model import OwnerControlSpec
from ..recipient_control_model import two_controller_creature_transfer_query
from ..source_continuity import SourceContinuitySnapshot


def _validate_control(actor, controller, duration, source, reason, *, allow_next_cleanup=False):
    if any(type(value) is not str or not value for value in (actor, controller, reason)):
        raise ValueError("Control intents require an actor, controller and reason")
    if actor != controller:
        raise ValueError("This control family is acquired by the resolving controller")
    if not isinstance(duration, ContinuousEffectDuration) or not (
        duration.source_bound or duration in {duration.UNTIL_END_OF_TURN, duration.ZONE_OBJECT}
        or allow_next_cleanup and duration is duration.UNTIL_END_OF_NEXT_TURN
    ):
        raise ValueError("Control intent duration is unsupported")
    if not isinstance(source, ResolutionEffectSource):
        raise ValueError("Control intents require a typed original source")


@dataclass(frozen=True, slots=True)
class GainControlIntent:
    actor: str
    object_ref: str
    controller: str
    duration: ContinuousEffectDuration
    source: ResolutionEffectSource
    reason: str
    history_snapshot: SourceContinuitySnapshot | None = None
    resolution_timestamp: int | None = None
    recipient_identity: ContinuousObjectIdentity | None = None

    def __post_init__(self) -> None:
        _validate_control(self.actor, self.controller, self.duration, self.source, self.reason, allow_next_cleanup=True)
        if (self.duration is ContinuousEffectDuration.UNTIL_END_OF_NEXT_TURN) != (self.recipient_identity is not None):
            raise ValueError("Next-turn cleanup control requires its exact original recipient")
        if self.recipient_identity is not None and not isinstance(self.recipient_identity, ContinuousObjectIdentity):
            raise ValueError("An original control recipient requires its typed incarnation")
        if type(self.object_ref) is not str or not self.object_ref:
            raise ValueError("Control intents require one public permanent reference")
        if self.history_snapshot is not None and not isinstance(self.history_snapshot, SourceContinuitySnapshot):
            raise ValueError("Control duration history must be a typed retained snapshot")
        if self.resolution_timestamp is not None and (
            type(self.resolution_timestamp) is not int or self.resolution_timestamp < 0
        ):
            raise ValueError("Control resolution timestamp must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class GainSpellControlIntent:
    actor: str
    object_ref: str
    source: ResolutionEffectSource
    reason: str

    def __post_init__(self):
        _validate_control(self.actor, self.actor, ContinuousEffectDuration.ZONE_OBJECT, self.source, self.reason)
        if type(self.object_ref) is not str or not self.object_ref:
            raise ValueError("Spell control requires one current public stack reference")


@dataclass(frozen=True, slots=True)
class DirectionalControlIntent:
    actor: str
    direction: str
    source: ResolutionEffectSource
    reason: str

    def __post_init__(self):
        from ..rules.directional_control import DIRECTIONS

        _validate_control(self.actor, self.actor, ContinuousEffectDuration.ZONE_OBJECT, self.source, self.reason)
        if self.direction not in DIRECTIONS or self.source.object_id is None:
            raise ValueError("Directional control requires a chosen direction and the original source incarnation")


@dataclass(frozen=True, slots=True)
class GainControlSetIntent:
    actor: str
    predicate: ObjectQuerySpec
    controller: str
    duration: ContinuousEffectDuration
    source: ResolutionEffectSource
    steps: tuple[str, ...]
    reason: str
    opponent: str | None = None
    target_refs: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        _validate_control(self.actor, self.controller, self.duration, self.source, self.reason)
        if not isinstance(self.predicate, ObjectQuerySpec):
            raise ValueError("Control-set intents require a typed public query")
        if self.duration.source_bound:
            raise ValueError("This control-set family cannot add a source duration")
        allowed = {
            ("gain_control",), ("untap", "gain_control"), ("gain_control", "untap"),
            ("gain_control", "haste"), ("untap", "gain_control", "haste"),
            ("gain_control", "untap", "haste"),
        }
        if type(self.steps) is not tuple or self.steps not in allowed:
            raise ValueError("Control-set instruction order is unsupported")
        if "haste" in self.steps and self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN:
            raise ValueError("Control-set haste requires its printed end-of-turn duration")
        if self.opponent is not None and (
            type(self.opponent) is not str or not self.opponent or self.opponent == self.actor
            or self.predicate != two_controller_creature_transfer_query()
            or self.steps != ("untap", "gain_control", "haste")
        ):
            raise ValueError("Two-controller creature transfer requires the exact opposing groups and result order")
        if self.target_refs is not None:
            from ..rules.attached_temporary_control import ATTACHED_CONTROL_RECEIVERS
            from ..rules.targeted_control_set import TARGETED_CONTROL_SET_RECEIVERS, TARGETED_CONTROL_SET_STEPS

            if (self.opponent is not None or self.controller != self.actor
                    or self.predicate not in (TARGETED_CONTROL_SET_RECEIVERS, ATTACHED_CONTROL_RECEIVERS)
                    or self.steps != TARGETED_CONTROL_SET_STEPS
                    or type(self.target_refs) is not tuple or any(type(ref) is not str or not ref for ref in self.target_refs)
                    or len(set(self.target_refs)) != len(self.target_refs)
                    or (self.predicate == ATTACHED_CONTROL_RECEIVERS and len(self.target_refs) != 1)):
                raise ValueError("Explicit control sets require their original references and printed order")


@dataclass(frozen=True,slots=True)
class GiveControlIntent:
    actor: str
    object_ref: str
    controller: str
    source: ResolutionEffectSource
    reason: str
    duration: ContinuousEffectDuration=field(init=False,default=ContinuousEffectDuration.ZONE_OBJECT)
    history_snapshot: SourceContinuitySnapshot | None=field(init=False,default=None)
    resolution_timestamp: int | None=field(init=False,default=None)

    def __post_init__(self):
        _validate_control(self.actor,self.actor,self.duration,self.source,self.reason)
        if any(type(value) is not str or not value for value in (self.object_ref,self.controller)):
            raise ValueError('Recipient control requires a permanent and explicit acquiring player')


@dataclass(frozen=True, slots=True)
class ReflexiveSourceControlIntent(GiveControlIntent):
    def __post_init__(self):
        GiveControlIntent.__post_init__(self)
        if (self.object_ref != self.source.card_ref or self.source.object_id is None
                or self.source.logical_object_id is None or self.actor == self.controller):
            raise ValueError("Reflexive control requires its original source and opposing recipient")


@dataclass(frozen=True, slots=True)
class UpkeepSourceHandoffIntent(GiveControlIntent):
    """The typed result of choosing the original upkeep handoff."""
    duration: ContinuousEffectDuration = field(init=False, default=ContinuousEffectDuration.UNTIL_END_OF_TURN)

    def __post_init__(self):
        GiveControlIntent.__post_init__(self)
        if (self.controller == self.actor or self.object_ref != self.source.card_ref
                or self.source.object_id is None or self.source.logical_object_id is None):
            raise ValueError("The upkeep handoff requires the original source and an opposing recipient")


@dataclass(frozen=True,slots=True)
class GiveControlSetIntent:
    actor: str
    controller: str
    exclude_source_ref: str | None
    source: ResolutionEffectSource
    reason: str

    def __post_init__(self):
        _validate_control(self.actor,self.actor,ContinuousEffectDuration.ZONE_OBJECT,self.source,self.reason)
        if type(self.controller) is not str or not self.controller:
            raise ValueError('Recipient control set requires an explicit acquiring player')
        if self.exclude_source_ref is not None and (type(self.exclude_source_ref) is not str or not self.exclude_source_ref):
            raise ValueError('Recipient control set requires its original source reference')


@dataclass(frozen=True,slots=True)
class RestoreOwnerControlIntent:
    actor: str
    spec: OwnerControlSpec
    source: ResolutionEffectSource
    reason: str

    def __post_init__(self):
        _validate_control(self.actor,self.actor,ContinuousEffectDuration.ZONE_OBJECT,self.source,self.reason)
        if not isinstance(self.spec,OwnerControlSpec):raise ValueError('Owner control requires its typed scope')


@dataclass(frozen=True, slots=True)
class ExchangePermanentControlIntent:
    actor: str
    references: tuple[str, str]
    source: ResolutionEffectSource
    reason: str

    def __post_init__(self):
        _validate_control(self.actor, self.actor, ContinuousEffectDuration.ZONE_OBJECT, self.source, self.reason)
        if (type(self.references) is not tuple or len(self.references) != 2
                or any(type(ref) is not str or not ref for ref in self.references)):
            raise ValueError("Permanent control exchange requires two public references")


ControlIntent = GainControlIntent | GainSpellControlIntent | GainControlSetIntent | GiveControlIntent | GiveControlSetIntent | RestoreOwnerControlIntent | ExchangePermanentControlIntent
