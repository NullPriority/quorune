"""Materialize fixed ninjutsu in the hand's current ability layer."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256

from ..ability_fragments import ability_fragment_from_dict, ability_fragment_to_dict
from ..card_programs.admission import program_has_complete_card_program_admission
from ..combat_entry_activations import FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID, NINJUTSU_EFFECT_OPERATION
from ..continuous_effect_model import ContinuousEffect, ContinuousEffectOrigin, ContinuousOperation, Layer
from ..granted_activation_model import (
    GRANTED_NINJUTSU_VERSION, HAND_NINJUTSU_GRANT_CAPABILITY, GrantedActivatedAbilitySpec,
)
from ..object_predicate import ObjectQuerySpec
from .combat_entry_activations import fixed_ninjutsu_specs_from_descriptors
from .component_registry import exact_fields
from .context import SemanticNodeError

HAND_NINJUTSU_GRANT_HANDLER = "continuous.ability.hand-ninjutsu-grant.v1"
HAND_NINJUTSU_RECIPIENT = ObjectQuerySpec(zones=("hand",), types_all=("creature",))


@dataclass(frozen=True, slots=True)
class HandNinjutsuGrantHandler:
    handler_id: str = HAND_NINJUTSU_GRANT_HANDLER
    schema_version: int = 1
    family: str = HAND_NINJUTSU_GRANT_CAPABILITY
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("611.3a", "611.3b", "613.1f", "702.49a")
    capability_dependencies: tuple[str, ...] = (
        HAND_NINJUTSU_GRANT_CAPABILITY, FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID,
    )

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "predicate", "modifier"},
                     field="hand ninjutsu grant")
        if (descriptor["handler_id"] != self.handler_id or descriptor["event"] != self.event
                or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version):
            raise SemanticNodeError("Hand ninjutsu grant requires its closed handler and event")
        modifier = descriptor["modifier"]
        if not isinstance(modifier, Mapping):
            raise SemanticNodeError("Hand ninjutsu grant requires a typed modifier")
        exact_fields(modifier, {"add_ability_fragments"}, field="hand ninjutsu modifier")
        values = modifier["add_ability_fragments"]
        if not isinstance(values, list) or len(values) != 1:
            raise SemanticNodeError("Hand ninjutsu grant requires one activation fragment")
        try:
            query = ObjectQuerySpec.from_dict(descriptor["predicate"])
            fragment = ability_fragment_from_dict(values[0])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if (query != HAND_NINJUTSU_RECIPIENT or not isinstance(fragment, GrantedActivatedAbilitySpec)
                or fragment.schema_version != GRANTED_NINJUTSU_VERSION or fragment.grant_incarnation is not None):
            raise SemanticNodeError("Hand ninjutsu grant requires its creature recipient and unbound activation")
        return query, fragment

    def may_affect_static_components(self, descriptor):
        self.validate(descriptor)
        return False

    def lower(self, descriptor, context):
        query, fragment = self.validate(descriptor)
        if (not context.source_logical_object_id
                or fragment.semantic_key.partition(":")[0] != context.component_id.partition(":")[0]):
            raise SemanticNodeError("A ninjutsu grant requires its own compiled activation and source incarnation")
        bound = replace(fragment, grant_incarnation=context.source_logical_object_id)
        return (ContinuousEffect(
            effect_id=f"{context.source_object_id}:{context.component_id}",
            source_id=context.source_object_id,
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=context.source_timestamp,
            operations=(ContinuousOperation("add_ability_fragment", ability_fragment_to_dict(bound)),),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=replace(query, owner=context.source_controller),
        ),)


def granted_ninjutsu_activation(host, fragment):
    program = host.semantics.get(fragment.semantic_key)
    if (fragment.grant_incarnation is None or program is None
            or program.ability_id != fragment.ability_id or program.active_zone != "hand"
            or program.event != "activate" or program.provenance.get("granted_only") is not True
            or program.target_schema is not None or program.event_condition is not None
            or tuple(program.effects) != ({
                "op": NINJUTSU_EFFECT_OPERATION, "attack_target": "$context.fixed_combat_return",
            },)
            or not program_has_complete_card_program_admission(program)
            or not host.semantic_program_is_current_trusted(program)):
        return None
    try:
        specs = fixed_ninjutsu_specs_from_descriptors(program.handlers)
    except ValueError:
        return None
    if len(specs) != 1 or specs[0].commander:
        return None
    canonical = specs[0].to_activated_ability()
    if (specs[0].cost_text != fragment.cost_text or canonical.effect_text != fragment.effect_text
            or {key: value for key, value in canonical.mana.items() if value} != fragment.mana_bundle):
        return None
    suffix = sha256(fragment.grant_incarnation.encode("utf-8")).hexdigest()[:20]
    return replace(canonical, ability_id=f"{fragment.ability_id}#ninjutsu-{suffix}", builtin_semantic_key=program.key)
