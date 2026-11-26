"""Offer private discard choices for a covered prospective artifact."""

from dataclasses import dataclass

from ..card_program_faces import program_matches_face
from ..replacement_effects import ReplacementEffect, ReplacementClass, ReserveZoneChange, SetField
from ..rules.entry_land_discard import EntryLandDiscard, FIELD, HANDLER, REQUIREMENTS, descriptor
from ..replacement.immutable import thaw_value
from .context import SemanticNodeError
from .self_entry_counters import program_static_component_is_applicable


@dataclass(frozen=True, slots=True)
class EntryLandDiscardHandler:
    handler_id: str = HANDLER
    schema_version: int = 1
    family: str = "replacement.zone.entry_land_discard"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = ("118.11", "614.1c", "614.12", "616.1", "701.8a")
    capability_dependencies: tuple[str, ...] = REQUIREMENTS

    def validate(self, value):
        if (thaw_value(value) != descriptor() or type(value.get("schema_version")) is not int):
            raise SemanticNodeError("Entry land discard has a closed version-one descriptor")

    def lower(self, value, context):
        self.validate(value)
        return ()

    def subject_replacement_effects(self, value, *, subject, component_id):
        self.validate(value)
        return ()


def entry_land_discard_effects(host, subject, *, prospective_objects=None):
    if subject.destination != "battlefield" or subject.destination_controller is None:
        return ()
    source = (prospective_objects or {}).get(subject.object_id) or host.state.cards.get(subject.object_id)
    record = host.card_record(source) if source is not None else None
    if record is None:
        return ()
    effects = []
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id, active_zone="all", event="zone.change"):
        if (not host.semantic_program_is_current_trusted(program) or not program_matches_face(record, program, source)
                or not program_static_component_is_applicable(
                    host, program, source, prospective_zone="battlefield",
                    prospective_controller=subject.destination_controller)):
            continue
        for index, handler in enumerate(program.handlers):
            if handler.get("handler_id") == HANDLER:
                EntryLandDiscardHandler().validate(handler)
                effects.extend(entry_land_discard_options(host, subject, component_id=f"{program.key}:{index}"))
    return tuple(effects)


def entry_land_discard_options(host, subject, *, component_id):
    actor = subject.destination_controller
    if actor not in host.state.players:
        return ()
    candidates = []
    for oid in host.state.players[actor].zones["hand"]:
        if oid == subject.object_id:
            continue
        card = host.state.cards[oid]
        data = host._effective_card_data(card)
        if card.is_card_object and "land" in host._type_parts(str(data.get("type_line") or ""))[0]:
            candidates.append(card)
    conditions = {"destination": {"eq": "battlefield"}, "object_ref": {"eq": subject.object_ref},
                  FIELD: {"eq": None}}
    choices = [
        (EntryLandDiscard(card.object_id, card.ref, card.logical_object_id, actor).to_dict(),
         f"Discard {card.printed_name} ({card.ref})")
        for card in sorted(candidates, key=lambda card: card.ref)
    ]
    choices.append(("decline", "Decline and put the entering object into its owner's graveyard"))
    return tuple(ReplacementEffect(
        effect_id=f"{HANDLER}:{subject.object_ref}:{component_id}:{index}",
        source_id=subject.object_ref, event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
        conditions=conditions, label=label,
        operations=(SetField(FIELD, selection),
                    *((SetField("destination", "graveyard"),) if selection == "decline"
                           else (ReserveZoneChange((selection["object_id"],)),))),
    ) for index, (selection, label) in enumerate(choices))
