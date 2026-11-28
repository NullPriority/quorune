"""An admitted token trigger recorded by a committed zone replacement."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace

from ..graveyard_exile_model import (
    GRAVEYARD_EXILE_DEPENDENCIES, GRAVEYARD_EXILE_HANDLER, GraveyardExileScope, GraveyardExileSpec,
)
from ..reflexive_trigger_model import REFLEXIVE_PAYMENT_CAPABILITY, ReflexiveTriggerProgram
from ..replacement.operations import SetField
from .reflexive_payment_shapes import reflexive_program_capabilities


HANDLER = "replacement.zone.graveyard-exile-token-trigger.v1"
CAPABILITY = "zone.change.qualified_exile_token_trigger"
TOKEN_CAPABILITY = "token.creation.fixed_definition"
DEPENDENCIES = (*GRAVEYARD_EXILE_DEPENDENCIES, CAPABILITY, REFLEXIVE_PAYMENT_CAPABILITY,
                TOKEN_CAPABILITY, "trigger.placement.apnap")
PAYLOAD_FIELD = "zone_replacement_token_trigger"


def validate_token_consequence(program):
    effect = program.effects[0]
    if (program.schema_version != 1 or len(program.effects) != 1 or program.target_schema is not None
            or effect.get("op") != "create_token" or type(effect.get("quantity")) is not int
            or effect["quantity"] != 1 or effect.get("controller") != "$controller"
            or reflexive_program_capabilities(program) != (TOKEN_CAPABILITY,)):
        raise ValueError("A replacement token trigger requires one fixed untargeted token")
    return program


def descriptor(spec, program):
    return {**spec.descriptor(), "handler_id": HANDLER,
            "token_consequence": validate_token_consequence(program).to_dict()}


def parse_descriptor(value):
    if not isinstance(value, Mapping) or "token_consequence" not in value or value.get("handler_id") != HANDLER:
        raise ValueError("A replacement token trigger requires its complete replacement descriptor")
    basic = {key: item for key, item in value.items() if key != "token_consequence"}
    basic["handler_id"] = GRAVEYARD_EXILE_HANDLER
    spec = GraveyardExileSpec.from_descriptor(basic)
    if spec.scope is not GraveyardExileScope.OPPONENT_CONTROLLED_CREATURE:
        raise ValueError("A replacement token trigger requires the original opposing creature scope")
    return spec, validate_token_consequence(ReflexiveTriggerProgram.from_dict(value["token_consequence"]))


@dataclass(frozen=True, slots=True)
class ReplacementTokenSource:
    ref: str
    object_id: str
    logical_id: str
    controller: str
    label: str

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (
            self.ref, self.object_id, self.logical_id, self.controller, self.label,
        )):
            raise ValueError("A replacement token trigger requires its original source incarnation")

    @classmethod
    def from_card(cls, card):
        return cls(card.ref, card.object_id, card.logical_object_id, card.controller, card.printed_name)

    def to_dict(self):
        return {"ref": self.ref, "object_id": self.object_id, "logical_id": self.logical_id,
                "controller": self.controller, "label": self.label}


def decode_replacement_token_trigger(raw):
    if not isinstance(raw, Mapping) or set(raw) != {"source", "program"}:
        raise ValueError("A replacement token trigger requires its original source and program")
    if not isinstance(raw["source"], Mapping):
        raise ValueError("A replacement token trigger requires a typed source")
    source = ReplacementTokenSource(**raw["source"])
    consequence = validate_token_consequence(ReflexiveTriggerProgram.from_dict(raw["program"]))
    return source, consequence


def validate_replacement_token_application(event, payload, value, effect_id):
    source, _consequence = decode_replacement_token_trigger(value)
    subject = GraveyardExileSpec(GraveyardExileScope.OPPONENT_CONTROLLED_CREATURE)
    from ..replacement.applicability import condition_matches

    if (payload.get(PAYLOAD_FIELD) is not None or payload.get("destination") != "exile"
            or not effect_id.startswith(f"{HANDLER}:{source.ref}:")
            or not condition_matches(subject.conditions(source.controller), event)):
        raise ValueError("A replacement token trigger requires its qualifying graveyard event and chosen source")


def publish_committed_replacement_token_trigger(host, prepared, replacement, card):
    """Enqueue exactly one interruptible trigger when this replacement exiled the object."""
    if prepared.destination != "exile" or card.zone != "exile":
        return
    fields = [operation for operation in replacement.operations
              if isinstance(operation, SetField) and operation.field == PAYLOAD_FIELD]
    if not fields:
        return
    if len(fields) != 1 or fields[0].value != prepared.event.payload.get(PAYLOAD_FIELD):
        raise ValueError("The committed token consequence differs from the applied replacement")
    source, consequence = decode_replacement_token_trigger(fields[0].value)
    if source.controller not in host.active_seats:
        return
    if source.ref != replacement.source_id:
        raise ValueError("The committed token consequence has a different replacement source")
    from .reflexive_triggers import enqueue_captured_reflexive_trigger

    parent = SimpleNamespace(controller=source.controller, label=source.label,
        ref=prepared.event.event_id, context={"source_logical_object_id": source.logical_id})
    enqueue_captured_reflexive_trigger(host, parent, consequence, source_id=source.object_id)
