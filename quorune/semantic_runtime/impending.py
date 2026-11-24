"""Validate the printed price in the canonical casting-cost registry."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..impending_model import (
    FixedManaImpendingSpec,
    IMPENDING_CAPABILITY,
    IMPENDING_COST_EVENT,
    IMPENDING_HANDLER_ID,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedManaImpendingHandler:
    handler_id: str = IMPENDING_HANDLER_ID
    schema_version: int = 1
    family: str = IMPENDING_CAPABILITY
    event: str = IMPENDING_COST_EVENT
    rule_references: tuple[str, ...] = ("601.2b", "601.2f", "702.176a")
    capability_dependencies: tuple[str, ...] = (IMPENDING_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> FixedManaImpendingSpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, "impending"},
            field="fixed-mana Impending",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
            or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True
        ):
            raise SemanticNodeError("Impending requires its exact price and complete-card admission")
        try:
            return FixedManaImpendingSpec.from_dict(descriptor["impending"])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor: Mapping[str, Any], context: object) -> tuple[FixedManaImpendingSpec, ...]:
        del context
        return (self.validate(descriptor),)


def prospective_impending_entry_types(host, card, destination, controller, characteristics, tags, counters):
    """Capture the real layer-four view of the paid entrant with a time counter."""
    from dataclasses import replace

    from ..impending_model import PAID_IMPENDING_FIELD
    from ..replacement.entry_counter_types import EntryTimeCounterTypes
    from ..rules.impending import IMPENDING_ENTRY_RULE, impending_entry_counters, resolving_impending_spec
    from .fixed_characteristic_settings import prospective_creature_type_characteristics

    paid_counters = tuple(counter for counter in counters if counter.rule_id == IMPENDING_ENTRY_RULE)
    if not paid_counters:
        return None
    stack_items = tuple(item for item in host.state.stack if item.card_object_id == card.object_id)
    if len(stack_items) != 1:
        raise SemanticNodeError("Impending preview needs one bound stack incarnation")
    item = stack_items[0]
    spec = resolving_impending_spec(item, card)
    if spec is None or paid_counters != impending_entry_counters(item, card, destination):
        raise SemanticNodeError("Impending preview does not match the prepared counter instruction")
    if "creature" not in tags:
        return None
    preview = replace(card, counters={"time": 1},
                      annotations={**card.annotations, PAID_IMPENDING_FIELD: spec.to_dict()})
    typed = prospective_creature_type_characteristics(
        host, preview, characteristics, destination_controller=controller,
    )
    with_counter = tuple(sorted(set().union(*host._type_parts(str(typed["type_line"])))))
    if with_counter == tags:
        raise SemanticNodeError("Paid Impending entry lacks its prospective type rule")
    return EntryTimeCounterTypes(tags, with_counter)
