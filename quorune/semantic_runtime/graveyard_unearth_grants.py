"""Materialize current battlefield Unearth grants in the graveyard ability layer."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..ability_fragments import ability_fragment_from_dict, ability_fragment_to_dict
from ..characteristic_read_frames import memoized_characteristic_read
from ..continuous_effect_model import ContinuousEffect, ContinuousEffectOrigin, ContinuousOperation, Layer
from ..granted_activation_model import (
    GRANTED_UNEARTH_VERSION, GRAVEYARD_UNEARTH_GRANT_CAPABILITY, GrantedActivatedAbilitySpec,
)
from ..rules.graveyard_unearth_grant import matches_graveyard_unearth_recipient
from ..object_predicate import ObjectQuerySpec
from .component_registry import exact_fields
from .context import SemanticNodeError

GRAVEYARD_UNEARTH_GRANT_HANDLER = "continuous.ability.graveyard-unearth-grant.v1"


@dataclass(frozen=True, slots=True)
class GraveyardUnearthGrantHandler:
    handler_id: str = GRAVEYARD_UNEARTH_GRANT_HANDLER
    schema_version: int = 1
    family: str = "continuous.ability.graveyard_unearth_grant"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("611.3a", "611.3b", "613.1f", "702.84a")
    capability_dependencies: tuple[str, ...] = (
        GRAVEYARD_UNEARTH_GRANT_CAPABILITY, "activation.unearth.fixed_mana",
    )

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "predicate", "modifier"},
                     field="graveyard Unearth grant")
        if (descriptor["handler_id"] != self.handler_id
                or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event):
            raise SemanticNodeError("Graveyard Unearth grant requires its closed handler and event")
        modifier = descriptor["modifier"]
        if not isinstance(modifier, Mapping):
            raise SemanticNodeError("Graveyard Unearth grant requires a typed modifier")
        exact_fields(modifier, {"add_ability_fragments"}, field="graveyard Unearth modifier")
        values = modifier["add_ability_fragments"]
        if not isinstance(values, list) or len(values) != 1:
            raise SemanticNodeError("Graveyard Unearth grant requires one activation fragment")
        try:
            query = ObjectQuerySpec.from_dict(descriptor["predicate"])
            fragment = ability_fragment_from_dict(values[0])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if (not matches_graveyard_unearth_recipient(query)
                or not isinstance(fragment, GrantedActivatedAbilitySpec)
                or fragment.schema_version != GRANTED_UNEARTH_VERSION or fragment.grant_incarnation is not None):
            raise SemanticNodeError("Graveyard Unearth grant requires its recipient and an unbound activation")
        return query, fragment

    def may_affect_static_components(self, descriptor):
        self.validate(descriptor)
        return False

    def lower(self, descriptor, context):
        query, fragment = self.validate(descriptor)
        if not context.source_logical_object_id:
            raise SemanticNodeError("The Unearth grant source incarnation is unavailable")
        if fragment.semantic_key.partition(":")[0] != context.component_id.partition(":")[0]:
            raise SemanticNodeError("The Unearth grant must name an activation from its own compiled card")
        bound = replace(fragment, grant_incarnation=context.source_logical_object_id)
        return (ContinuousEffect(
            effect_id=f"{context.source_object_id}:{context.component_id}",
            source_id=context.source_object_id, layer=Layer.ABILITY, sublayer="6",
            timestamp=context.source_timestamp,
            operations=(ContinuousOperation("add_ability_fragment", ability_fragment_to_dict(bound)),),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=replace(query, owner=context.source_controller),
        ),)


def graveyard_unearth_effects_for_card(host, card, *, maximum_layer=None):
    if card.zone != "graveyard" or card.is_token or (maximum_layer is not None and maximum_layer < Layer.ABILITY):
        return ()

    def collect():
        if not any(descriptor.get("handler_id") == GRAVEYARD_UNEARTH_GRANT_HANDLER
                   for program in host.semantics.programs_for_event("characteristics.evaluate")
                   for descriptor in program.handlers):
            return ()
        from ..card_programs.runtime import collect_card_program_continuous_effects

        keys = host._effective_static_component_key_map()
        return collect_card_program_continuous_effects(
            host.state, host.semantics, host.semantic_program_is_current_trusted,
            maximum_layer=Layer.ABILITY, static_component_resolver=lambda source: keys.get(source.object_id, ()),
            early_static_component_resolver=host._static_component_keys_at_layer,
            copied_static_source_resolver=host._copied_static_source_fragments,
            ability_source_participation=host._static_component_presence_at_ability_start,
            handler_ids=frozenset({GRAVEYARD_UNEARTH_GRANT_HANDLER}),
        )

    return tuple(effect for effect in memoized_characteristic_read(host, ("graveyard-unearth-grants",), collect)
                 if effect.applies.owner == card.owner)
