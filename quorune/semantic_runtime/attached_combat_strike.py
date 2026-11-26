"""Closed Equipment first strike qualified by a live combat counterpart."""

from dataclasses import dataclass
from typing import Mapping

from ..attachments import attached_object_identity
from ..combat import current_block_counterpart_ids
from ..continuous_effect_model import (
    ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation, ContinuousOperation, Layer,
)
from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec
from ..object_query import object_matches_query
from .component_registry import exact_fields
from .context import SemanticNodeError


EQUIPPED_COMBAT_STRIKE_HANDLER = "continuous.attached.combat-counterpart-first-strike.v1"
EQUIPPED_COMBAT_STRIKE_CAPABILITY = "continuous.attached.combat_counterpart_first_strike"
EQUIPPED_COMBAT_STRIKE_PREREQUISITES = (
    "combat.declaration.typed_components",
    "combat.damage.participation.strike_steps",
    "continuous.attached.fixed_characteristics",
)
_CREATURE_RECEIVER = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))


def combat_counterpart_query(subtypes: tuple[str, ...]) -> ObjectQuerySpec:
    if (len(subtypes) != 2 or len(set(subtypes)) != 2
            or any(canonical_creature_subtype(value) != value for value in subtypes)):
        raise SemanticNodeError("Combat counterpart requires two distinct canonical creature subtypes")
    return ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), subtypes_any=subtypes)


def equipped_block_counterpart_facts(state, source, resolve):
    """Read only battlefield creatures in live block pairs of the current recipient."""
    attachment = attached_object_identity(state.cards, source)
    if attachment is None:
        return ()
    combat = state.combat
    counterpart_ids = current_block_counterpart_ids(
        combat.blockers, attachment.object_id, live_attackers=combat.attackers,
    )
    return tuple(
        resolve(card) for object_id in counterpart_ids
        if (card := state.cards.get(object_id)) is not None
        and card.zone == "battlefield" and not card.phased_out
    )


@dataclass(frozen=True, slots=True)
class EquippedCombatStrikeHandler:
    handler_id: str = EQUIPPED_COMBAT_STRIKE_HANDLER
    schema_version: int = 1
    family: str = EQUIPPED_COMBAT_STRIKE_CAPABILITY
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = (
        "301.5a", "506.4", "509.1g", "509.1h", "510.4", "611.3a", "613.1f", "702.7a",
    )
    capability_dependencies: tuple[str, ...] = (
        EQUIPPED_COMBAT_STRIKE_CAPABILITY, *EQUIPPED_COMBAT_STRIKE_PREREQUISITES,
    )

    def validate(self, descriptor: Mapping) -> ObjectQuerySpec:
        exact_fields(
            descriptor, {"handler_id", "schema_version", "event", "counterpart"},
            field="Equipped combat first strike",
        )
        if (descriptor["handler_id"] != self.handler_id or descriptor["event"] != self.event
                or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version):
            raise SemanticNodeError("Equipped combat first strike descriptor identity changed")
        query = ObjectQuerySpec.from_dict(descriptor["counterpart"])
        if query != combat_counterpart_query(query.subtypes_any):
            raise SemanticNodeError("Equipped combat first strike requires a closed creature-subtype query")
        return query

    def descriptor_source_component_layer(self, descriptor: Mapping) -> Layer:
        self.validate(descriptor)
        return Layer.ABILITY

    def lower(self, descriptor, context) -> tuple[ContinuousEffect, ...]:
        query = self.validate(descriptor)
        if context.attached_object is None or not any(
            object_matches_query(card, query) for card in context.public_type_objects or ()
        ):
            return ()
        return (ContinuousEffect(
            effect_id=f"{context.source_object_id}:{context.component_id}:6",
            source_id=context.source_object_id,
            timestamp=context.source_timestamp,
            layer=Layer.ABILITY,
            sublayer="6",
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=_CREATURE_RECEIVER,
            relation=ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT,
            related_object=context.attached_object,
            operations=(ContinuousOperation("add_ability", "First Strike"),),
        ),)
