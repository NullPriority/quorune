"""Controller tokens from committed original target destruction."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap, thaw_value
from ..target_cardinality import (
    ANY_NUMBER_TARGET_CAPABILITY, AnnouncedTargetCardinality, selected_target_count_descriptor,
    validate_selected_target_count,
)
from .committed_quantity_followups import (
    fixed_followup_capabilities, fixed_quantity_effect, token_followup_from_committed_controllers,
)
from .effect_shape_walkers import nested_effects


ANNOUNCED_DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY = "permanent.destroy.announced_targets_controller_tokens"
OPTIONAL_GRAVEYARD_CONTROLLER_TOKEN_CAPABILITY = "permanent.destroy.optional_target_graveyard_controller_tokens"
_OPTIONAL_GRAVEYARD_TARGET = FrozenMap({
    "zones": ["battlefield"], "categories": ["permanent"], "types_none": ["creature"], "up_to": 3,
})
ANNOUNCED_DESTRUCTION_CONTROLLER_TOKEN_TARGET = FrozenMap({
    "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
    "count_parameter": AnnouncedTargetCardinality(
        selection="up_to", parameter="any_number", schema_version=2,
    ).to_dict(),
})


@dataclass(frozen=True, slots=True)
class AnnouncedDestructionControllerTokens:
    token: Mapping
    schema_version: int = 1

    def __post_init__(self):
        token = fixed_quantity_effect(self.token)
        definition = token.get("characteristics", {})
        if (type(self.schema_version) is not int or self.schema_version not in {1, 2}
                or token.get("op") != "create_token" or "tapped" in token
                or not definition.get("type_line", "").startswith("Token Creature — ")):
            raise ValueError("Selected destruction requires one ordinary fixed creature token")
        object.__setattr__(self, "token", token)

    def to_dict(self):
        return {"schema_version": self.schema_version, "token": thaw_value(self.token)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "token"}:
            raise ValueError("Selected destruction token result has a closed descriptor")
        return cls(value["token"], value["schema_version"])

    @property
    def requires_graveyard(self):
        return self.schema_version == 2

    def effect(self):
        count = (
            {"maximum_targets": 3}
            if self.requires_graveyard else
            {"announced_target_count": True, "maximum_targets": selected_target_count_descriptor()}
        )
        return {"op": "destroy_targets", "cards": "$targets", **count, "followup": self.to_dict()}

    @property
    def dependencies(self):
        scope = ((OPTIONAL_GRAVEYARD_CONTROLLER_TOKEN_CAPABILITY,)
                 if self.requires_graveyard else
                 (ANNOUNCED_DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY, ANY_NUMBER_TARGET_CAPABILITY))
        return (*scope, "permanent.destroy.effect", "resolution.effect.fixed_homogeneous_target_set",
                "target.revalidate_resolution", "token.creation.fixed_distributed",
                *fixed_followup_capabilities(self.token))

    def from_committed_controllers(self, controllers):
        return token_followup_from_committed_controllers(self.token, controllers)


def uses_announced_destruction_controller_tokens(effect):
    from .controlled_destruction_source_counters import uses_controlled_destruction_source_counters

    return (isinstance(effect, Mapping) and effect.get("op") == "destroy_targets" and "followup" in effect
            and not uses_controlled_destruction_source_counters(effect))


def uses_optional_graveyard_controller_tokens(effect):
    if not uses_announced_destruction_controller_tokens(effect):
        return False
    followup = effect["followup"]
    return isinstance(followup, Mapping) and followup.get("schema_version") == 2


def optional_graveyard_controller_token_dependencies(effect, target_schema):
    if (not uses_optional_graveyard_controller_tokens(effect)
            or not isinstance(target_schema, Mapping) or FrozenMap(target_schema) != _OPTIONAL_GRAVEYARD_TARGET
            or set(effect) != {"op", "cards", "maximum_targets", "followup"}
            or effect["cards"] != "$targets" or type(effect["maximum_targets"]) is not int
            or effect["maximum_targets"] != 3):
        raise ValueError("Graveyard controller tokens require the declared up-to-three noncreature target set")
    return AnnouncedDestructionControllerTokens.from_dict(effect["followup"]).dependencies


def announced_destruction_controller_token_dependencies(effect, target_schema):
    if (not uses_announced_destruction_controller_tokens(effect)
            or uses_optional_graveyard_controller_tokens(effect)
            or set(effect) != {"op", "cards", "announced_target_count", "maximum_targets", "followup"}
            or effect["cards"] != "$targets" or effect["announced_target_count"] is not True
            or not isinstance(target_schema, Mapping)
            or FrozenMap(target_schema) != ANNOUNCED_DESTRUCTION_CONTROLLER_TOKEN_TARGET):
        raise ValueError("Selected destruction tokens need the original unrestricted declared creature target set")
    validate_selected_target_count(effect["maximum_targets"])
    AnnouncedTargetCardinality.from_dict(target_schema["count_parameter"])
    return AnnouncedDestructionControllerTokens.from_dict(effect["followup"]).dependencies


def announced_destruction_controller_token_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                   if uses_announced_destruction_controller_tokens(effect))
    owners = {ANNOUNCED_DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY, OPTIONAL_GRAVEYARD_CONTROLLER_TOKEN_CAPABILITY}
    if not marked and not owners.intersection(program.capability_dependencies):
        return ()
    if len(marked) == 1 and uses_optional_graveyard_controller_tokens(marked[0]):
        wrapper = {"op": "offer_optional_effect", "player": "$controller", "effects": [marked[0]]}
        if (tuple(FrozenMap(effect) for effect in program.effects) != (FrozenMap(wrapper),)
                or program.handlers or program.event_condition is not None or program.cost_schema is not None
                or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")):
            raise ValueError("Graveyard controller tokens require the complete optional self-entry trigger")
        return ("effect.choice.optional_fixed",
                *optional_graveyard_controller_token_dependencies(marked[0], program.target_schema))
    if (len(marked) != 1 or marked != tuple(program.effects) or program.handlers or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("Selected destruction controller tokens require the original spell and target set")
    return announced_destruction_controller_token_dependencies(marked[0], program.target_schema)
