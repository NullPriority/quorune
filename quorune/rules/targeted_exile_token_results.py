"""Closed creature-token results of a committed targeted exile."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..fixed_token_production import FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID
from ..replacement.immutable import FrozenMap, thaw_value
from .committed_quantity_followups import fixed_followup_capabilities, fixed_quantity_effect


TARGET_EXILE_TOKEN_CAPABILITY = "permanent.exile.target_controller_token_result"
TARGET_EXILE_TOKEN_MECHANIC = "committed-target-exile-controller-tokens"
TARGET_EXILE_OPERATION = "exile_permanent_targets"


@dataclass(frozen=True, slots=True)
class TargetedExileTokenFollowup:
    token: FrozenMap

    def __post_init__(self) -> None:
        token = fixed_quantity_effect(self.token)
        if (
            token["op"] != "create_token" or "tapped" in token
            or not token["characteristics"]["type_line"].startswith("Token Creature — ")
        ):
            raise ValueError("Targeted exile results require a fixed creature token")
        object.__setattr__(self, "token", token)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, actor: str | None = None) -> "TargetedExileTokenFollowup":
        if (
            not isinstance(value, Mapping) or set(value) != {"schema_version", "token"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
        ):
            raise ValueError("Targeted exile token follow-up is malformed")
        token = value["token"]
        if actor is not None and isinstance(token, Mapping) and token.get("controller") == actor:
            token = {**token, "controller": "$controller"}
        return cls(token)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "token": thaw_value(self.token)}

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        return (
            TARGET_EXILE_TOKEN_CAPABILITY, FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID,
            "resolution.target_controller_information", "zone.change.destination_replacement",
            *fixed_followup_capabilities(self.token),
        )


def targeted_exile_token_payload_dependencies(effect) -> tuple[str, ...]:
    if effect.get("op") != TARGET_EXILE_OPERATION or "followup" not in effect:
        return ()
    return TargetedExileTokenFollowup.from_dict(effect["followup"]).capability_dependencies


def targeted_exile_token_node_capabilities(effect, target_schema, mechanics) -> tuple[str, ...]:
    from ..compiler.direct_target import direct_permanent_target_spec
    from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities

    if (
        TARGET_EXILE_TOKEN_MECHANIC not in mechanics
        or not isinstance(target_schema, Mapping)
        or set(effect) != {"op", "cards", "maximum_targets", "followup"}
    ):
        return ()
    schema = dict(target_schema)
    fields = set(schema) & {"count", "up_to"}
    if len(fields) != 1:
        return ()
    schema.pop(next(iter(fields)))
    if {**schema, "count": 1} != direct_permanent_target_spec("target creature").to_target_schema():
        return ()
    bare = {key: value for key, value in effect.items() if key != "followup"}
    try:
        required = targeted_exile_token_payload_dependencies(effect)
        owned = fixed_homogeneous_target_set_node_capabilities(
            effects=(bare,), target_schema=target_schema, mechanic_ids=mechanics,
        )
    except (TypeError, ValueError):
        return ()
    return (*owned, *required) if owned and required else ()
