"""Use the original land's basic status to untap the same resolving source."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from .committed_quantity_followups import CommittedQuantityFollowup
from .effect_shape_walkers import nested_effects


NONBASIC_LAND_SOURCE_UNTAP_KIND = "nonbasic_land_source_untap"
NONBASIC_LAND_SOURCE_UNTAP_CAPABILITY = "permanent.destroy.nonbasic_target_source_untap"
_LAND_TARGET = FrozenMap({
    "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["land"], "count": 1,
})
_UNTAP_SOURCE = FrozenMap({"op": "untap", "card": "$source.zone_object"})


@dataclass(frozen=True, slots=True)
class NonbasicLandSourceUntap:
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Nonbasic land source untap has an exact version")

    def to_dict(self):
        return {"kind": NONBASIC_LAND_SOURCE_UNTAP_KIND, "schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"kind", "schema_version"}
                or value["kind"] != NONBASIC_LAND_SOURCE_UNTAP_KIND):
            raise ValueError("Nonbasic land source untap requires its complete descriptor")
        return cls(value["schema_version"])

    def effect(self):
        return {"op": "destroy", "card": "$target.0", "followup": self.to_dict()}

    def committed_result(self, *, target_was_basic):
        if type(target_was_basic) is not bool:
            raise ValueError("Nonbasic land source untap requires the original target's basic fact")
        return CommittedQuantityFollowup(() if target_was_basic else (_UNTAP_SOURCE,))


def is_nonbasic_land_untap_descriptor(value):
    return isinstance(value, Mapping) and value.get("kind") == NONBASIC_LAND_SOURCE_UNTAP_KIND


def uses_nonbasic_land_source_untap(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "destroy"
            and is_nonbasic_land_untap_descriptor(effect.get("followup")))


def decode_single_destruction_followup(value):
    if is_nonbasic_land_untap_descriptor(value):
        return NonbasicLandSourceUntap.from_dict(value)
    from .artifact_destruction_treasure import ArtifactDestructionTreasure
    from .destroyed_creature_copy import DESTROYED_CREATURE_COPY_KIND, DestroyedCreatureCopy
    from .target_controller_library_reveal import TargetControllerLibraryReveal, is_target_controller_library_descriptor

    if isinstance(value, Mapping) and value.get("kind") == DESTROYED_CREATURE_COPY_KIND:
        return DestroyedCreatureCopy.from_dict(value)
    if is_target_controller_library_descriptor(value):
        return TargetControllerLibraryReveal.from_dict(value)
    return ArtifactDestructionTreasure.from_dict(value)


def nonbasic_land_source_untap_dependencies(effect, target_schema):
    if (not uses_nonbasic_land_source_untap(effect) or set(effect) != {"op", "card", "followup"}
            or effect["card"] != "$target.0" or not isinstance(target_schema, Mapping)
            or type(target_schema.get("count")) is not int or FrozenMap(target_schema) != _LAND_TARGET):
        raise ValueError("Nonbasic land source untap requires one direct land target")
    NonbasicLandSourceUntap.from_dict(effect["followup"])
    from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities

    return (NONBASIC_LAND_SOURCE_UNTAP_CAPABILITY, "permanent.destroy.effect", "permanent.untap.effect",
            "target.revalidate_resolution", *direct_target_predicate_capabilities(target_schema))


def nonbasic_land_source_untap_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if uses_nonbasic_land_source_untap(effect))
    if not marked and NONBASIC_LAND_SOURCE_UNTAP_CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(marked) != 1 or tuple(program.effects) != marked or len(program.handlers) != 1
            or (program.event, program.active_zone) != ("activate", "battlefield")
            or not isinstance(program.cost_schema, Mapping) or program.cost_schema.get("tap_source") is not True):
        raise ValueError("Nonbasic land source untap requires the original tapped-source battlefield activation")
    from ..compiler.activated_costs import activated_ability_cost
    from ..replacement.immutable import thaw_value
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if (ability.builtin_semantic_key != program.key or ability.zones != ("battlefield",)
            or ability.mana_ability or not ability.tap_source
            or activated_ability_cost(ability) != thaw_value(program.cost_schema)
            or thaw_value(ability.target_schema) != thaw_value(program.target_schema)):
        raise ValueError("Nonbasic land source untap requires its exact pinned activation and price")
    return nonbasic_land_source_untap_dependencies(marked[0], program.target_schema)
