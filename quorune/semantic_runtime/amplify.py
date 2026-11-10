from __future__ import annotations

"""Lower a trusted Amplify descriptor into a private entry choice."""

from dataclasses import dataclass

from ..rules.amplify import AMPLIFY_CAPABILITY, AMPLIFY_HANDLER, AmplifyHandCard, AmplifySpec
from ..creature_subtypes import canonical_creature_subtype
from ..replacement import ReplacementClass, ReplacementEffect
from ..replacement.amplify import AmplifyEntryOperation
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class AmplifyEntryHandler:
    handler_id: str = AMPLIFY_HANDLER
    schema_version: int = 1
    family: str = "replacement.zone.amplify"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = ("702.38a", "702.38b", "614.12", "616.1g", "122.6", "701.20a")
    capability_dependencies: tuple[str, ...] = (AMPLIFY_CAPABILITY, "counter.placement.quantity_replacement")

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "amplify"}, field="Amplify entry")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event):
            raise SemanticNodeError("Amplify entry identity changed")
        try:
            return AmplifySpec.from_dict(descriptor["amplify"])
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()

    def subject_replacement_effects(self, descriptor, *, subject, component_id):
        self.validate(descriptor)
        return ()


def hand_creature_types(host, card) -> tuple[str, ...]:
    data = host._effective_card_data(card)
    _, subtypes, _ = host._type_parts(str(data.get("type_line") or ""))
    return tuple(sorted({value for subtype in subtypes if (value := canonical_creature_subtype(subtype))}))


def amplify_replacement_effects(host, subject, descriptor, *, component_id):
    spec = AmplifyEntryHandler().validate(descriptor)
    controller = subject.destination_controller
    if controller is None or subject.destination != "battlefield" or subject.origin == "battlefield":
        return ()
    candidates = []
    for object_id in host.state.players[controller].zones["hand"]:
        card = host.state.cards[object_id]
        if card.object_id == subject.object_id or card.zone != "hand" or card.owner != controller:
            continue
        creature_types = hand_creature_types(host, card)
        if creature_types:
            candidates.append(AmplifyHandCard(card.object_id, card.logical_object_id, card.ref,
                controller, card.oracle_id, card.printed_name, creature_types))
    if not candidates:
        return ()
    operation = AmplifyEntryOperation(controller, spec, tuple(sorted(candidates, key=lambda card: card.ref)))
    return (ReplacementEffect(
        effect_id=f"{AMPLIFY_HANDLER}:{subject.object_ref}:{component_id}",
        source_id=subject.object_ref, event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
        conditions={"destination": {"eq": "battlefield"}, "object_ref": {"eq": subject.object_ref},
            "entry_copy_form": {"eq": None}},
        operations=(operation,), optional=True, label=f"{subject.object_ref}: Amplify {spec.multiplier}",
    ),)
