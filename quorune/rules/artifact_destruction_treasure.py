"""A fixed Treasure result from an actual selected artifact destruction."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap, thaw_value
from .committed_quantity_followups import CommittedQuantityFollowup, fixed_followup_capabilities, fixed_quantity_effect
from .effect_shape_walkers import nested_effects


ARTIFACT_DESTRUCTION_TREASURE_CAPABILITY = "permanent.destroy.target_artifact_treasure_result"
ARTIFACT_DESTRUCTION_TREASURE_TARGET = FrozenMap({
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["artifact", "land"],
})


@dataclass(frozen=True, slots=True)
class ArtifactDestructionTreasure:
    token: Mapping
    schema_version: int = 1

    def __post_init__(self):
        token = fixed_quantity_effect(self.token)
        characteristics = token.get("characteristics", {})
        if (type(self.schema_version) is not int or self.schema_version != 1
                or token.get("op") != "create_token" or "tapped" in token
                or token.get("name") != "Treasure Token"
                or characteristics.get("type_line") != "Token Artifact — Treasure"
                or characteristics.get("activated_ability_profile") != "tap_sac_any_color_mana_v1"):
            raise ValueError("Artifact destruction requires one ordinary Treasure as its committed result")
        object.__setattr__(self, "token", token)

    def to_dict(self):
        return {"schema_version": self.schema_version, "token": thaw_value(self.token)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "token"}:
            raise ValueError("The artifact destruction result has a closed descriptor")
        return cls(value["token"], value["schema_version"])

    def effect(self):
        return {"op": "destroy", "card": "$target.0", "followup": self.to_dict()}

    @property
    def dependencies(self):
        return ("permanent.destroy.effect", ARTIFACT_DESTRUCTION_TREASURE_CAPABILITY,
                "target.revalidate_resolution", *fixed_followup_capabilities(self.token))

    def committed_result(self, *, artifact, destroyed):
        if type(artifact) is not bool or type(destroyed) is not bool:
            raise ValueError("Artifact destruction requires the original snapshot and committed outcome")
        return CommittedQuantityFollowup((self.token,) if artifact and destroyed else ())


def uses_artifact_destruction_treasure(value):
    from .destroyed_creature_copy import uses_destroyed_creature_copy
    from .nonbasic_land_source_untap import uses_nonbasic_land_source_untap
    from .target_controller_library_reveal import uses_target_controller_library_reveal

    return (isinstance(value, Mapping) and value.get("op") == "destroy" and "followup" in value
            and not uses_nonbasic_land_source_untap(value) and not uses_destroyed_creature_copy(value)
            and not uses_target_controller_library_reveal(value))


def artifact_destruction_treasure_dependencies(effect, target_schema):
    if (not uses_artifact_destruction_treasure(effect) or set(effect) != {"op", "card", "followup"}
            or effect["card"] != "$target.0" or not isinstance(target_schema, Mapping)
            or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != ARTIFACT_DESTRUCTION_TREASURE_TARGET):
        raise ValueError("The committed artifact result requires its exact direct artifact-or-land target")
    from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities

    return (*ArtifactDestructionTreasure.from_dict(effect["followup"]).dependencies,
            *direct_target_predicate_capabilities(target_schema))


def artifact_destruction_treasure_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                   if uses_artifact_destruction_treasure(effect))
    if not marked and ARTIFACT_DESTRUCTION_TREASURE_CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(marked) != 1 or marked != tuple(program.effects) or program.handlers or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("The committed artifact result requires its original direct spell instruction")
    return artifact_destruction_treasure_dependencies(marked[0], program.target_schema)
