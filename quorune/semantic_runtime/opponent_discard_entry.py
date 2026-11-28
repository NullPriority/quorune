"""Resolve the source-local choice against sealed effect-origin discard facts."""

from dataclasses import dataclass

from ..discard_effect_origin import opponent_discard_origin
from ..opponent_discard_entry_model import (
    OPPONENT_DISCARD_ENTRY_CAPABILITY, OPPONENT_DISCARD_ENTRY_EVENT,
    OPPONENT_DISCARD_ENTRY_HANDLER, OpponentDiscardEntrySpec,
)
from ..replacement.model import ReplacementClass, ReplacementEffect
from ..replacement.operations import CreateAffectedObjectCounter, SetField
from ..zone_trigger_events import ZoneTransitionKind
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class OpponentDiscardEntryHandler:
    handler_id: str = OPPONENT_DISCARD_ENTRY_HANDLER
    schema_version: int = 1
    family: str = OPPONENT_DISCARD_ENTRY_CAPABILITY
    event: str = OPPONENT_DISCARD_ENTRY_EVENT
    rule_references: tuple[str, ...] = ("400.7", "614.1a", "614.6", "614.12", "616.1", "701.9a")
    capability_dependencies: tuple[str, ...] = (OPPONENT_DISCARD_ENTRY_CAPABILITY, "zone.change.destination_replacement")

    def validate(self, descriptor):
        try:
            return OpponentDiscardEntrySpec.from_descriptor(descriptor)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()

    def subject_replacement_effects(self, descriptor, *, subject, component_id):
        spec = self.validate(descriptor)
        origin = subject.discard_effect_origin
        if (subject.origin != "hand" or subject.destination != "graveyard" or not subject.is_card_object
                or subject.transition_kind is not ZoneTransitionKind.DISCARD or origin is None
                or "creature" not in subject.object_types
                or opponent_discard_origin(origin.to_dict(), discarding_player=subject.owner,
                                           opponents=subject.active_players) is None):
            return ()
        operations = (SetField("destination", "battlefield"),)
        if spec.entry_counters:
            operations += (CreateAffectedObjectCounter("+1/+1", spec.entry_counters, subject.owner, subject.object_ref),)
        return (ReplacementEffect(
            effect_id=f"{self.handler_id}:{subject.logical_object_id}:{component_id}",
            source_id=subject.object_ref, event_kind=self.event, replacement_class=ReplacementClass.OTHER,
            conditions={"origin": {"eq": "hand"}, "destination": {"eq": "graveyard"},
                        "transition_kind": {"eq": ZoneTransitionKind.DISCARD.value},
                        "logical_object_id": {"eq": subject.logical_object_id}},
            operations=operations,
            label="Put the discarded card onto the battlefield under its owner's control",
        ),)
