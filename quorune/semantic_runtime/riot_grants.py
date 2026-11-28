from __future__ import annotations

"""Build and consume separately granted Riot through the native entry owner."""

from dataclasses import dataclass, replace

from ..ability_fragments import ability_fragment_to_dict, canonical_ability_fragments
from ..card_program_faces import prospective_face_view
from ..continuous_effect_model import ContinuousEffect, ContinuousEffectOrigin, ContinuousOperation, Layer
from ..counter_permission import COUNTER_PROHIBITION_CAPABILITY
from ..object_predicate import ObjectQuerySpec
from ..riot import (
    GrantedRiotSpec, RIOT_GRANT_CAPABILITY, RIOT_GRANT_HANDLER_ID, RIOT_GRANTED_COMPONENT_PREFIX,
    riot_entry_handler_descriptor, riot_grant_handler_descriptor,
)
from .context import SemanticNodeError
from .entry_choices import RiotEntryChoiceHandler
from .zone_replacement_inputs import _entry_layer_characteristics, conditional_counter_entry_types


@dataclass(frozen=True, slots=True)
class NontokenRiotGrantHandler:
    handler_id: str = RIOT_GRANT_HANDLER_ID
    schema_version: int = 1
    family: str = RIOT_GRANT_CAPABILITY
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("611.3a", "611.3b", "613.1f", "614.12", "702.136a", "702.136b")
    capability_dependencies: tuple[str, ...] = (
        RIOT_GRANT_CAPABILITY, "counter.producer.riot", COUNTER_PROHIBITION_CAPABILITY,
    )

    def validate(self, descriptor):
        if (dict(descriptor) != riot_grant_handler_descriptor()
                or type(descriptor["schema_version"]) is not int):
            raise SemanticNodeError("A Riot grant requires the exact controlled nontoken component")

    def lower(self, descriptor, context):
        self.validate(descriptor)
        if context.source_logical_object_id is None:
            raise SemanticNodeError("A Riot grant requires the current granting incarnation")
        instance = f"{context.source_logical_object_id}:{context.component_id}"
        return (ContinuousEffect(
            effect_id=f"{context.source_object_id}:{context.component_id}:6",
            source_id=context.source_object_id,
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=context.source_timestamp,
            operations=(
                ContinuousOperation("add_ability", "Riot"),
                ContinuousOperation("add_ability_fragment", ability_fragment_to_dict(GrantedRiotSpec(instance))),
            ),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=ObjectQuerySpec(zones=("battlefield",), controller=context.source_controller,
                                    types_all=("creature",), token=False),
        ),)


def granted_riot_entry_effects(host, subject, card, *, copy_values=None, form_key=None):
    """Read the entrant's actual prospective layer-six grants, including the selected copied form."""
    if (subject.destination != "battlefield" or subject.destination_controller is None
            or not subject.is_card_object or not any(
                handler.get("handler_id") == RIOT_GRANT_HANDLER_ID
                for program in host.semantics.programs_for_event("characteristics.evaluate")
                for handler in program.handlers
            )):
        return ()
    face = prospective_face_view(host.card_record(card), card, subject.entry_face_id)
    entry_face = face.active_face if face.active_face == subject.entry_face_id else None
    _, effective = _entry_layer_characteristics(
        host, face, subject.destination_controller, maximum_layer=Layer.ABILITY,
        copy_values=copy_values, entry_face=entry_face,
    )
    fragments = tuple(fragment for fragment in canonical_ability_fragments(effective.get("ability_fragments", ()))
                      if isinstance(fragment, GrantedRiotSpec))
    if not fragments or "creature" not in conditional_counter_entry_types(
        host, face, subject.destination_controller, copy_values=copy_values, entry_face=entry_face,
    ):
        return ()
    policy = RiotEntryChoiceHandler()
    rows = []
    for fragment in fragments:
        identity = f"{RIOT_GRANTED_COMPONENT_PREFIX}{fragment.instance_id}"
        effect = policy.subject_replacement_effect(
            riot_entry_handler_descriptor(), subject=subject, component_id=f"{identity}:{form_key or 'original'}",
        )
        rows.append(replace(
            effect, conditions={**dict(effect.conditions), "entry_copy_form": {"eq": form_key}},
        ))
    return tuple(rows)
