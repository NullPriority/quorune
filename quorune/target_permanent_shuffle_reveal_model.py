"""Exact descriptors for the target owner's shuffle and reveal sequence."""

from collections.abc import Mapping
from dataclasses import dataclass

from .errors import GameRuleError
from .target_controller_information import owner_reference_index, resolve_target_owner_information


WARP_OPERATION = "target_permanent_shuffle_reveal"
WARP_MECHANIC = "target-permanent-shuffle-reveal"
WARP_CAPABILITY = "library.shuffle.target_permanent_then_reveal"
WARP_OWNER_BINDING = "_shuffle_reveal_owner"


@dataclass(frozen=True, slots=True)
class TargetPermanentShuffleRevealSpec:
    player: str | Mapping

    @classmethod
    def from_effect(cls, effect, *, resolved=False):
        if (
            not isinstance(effect, Mapping)
            or set(effect) != {"op", "schema_version", "player"}
            or effect["op"] != WARP_OPERATION
            or type(effect["schema_version"]) is not int
            or effect["schema_version"] != 1
        ):
            raise ValueError("Target-owner reveal requires its exact version-one effect")
        player = effect["player"]
        if resolved:
            if type(player) is not str or not player or player.startswith("$"):
                raise ValueError("Target-owner reveal requires a resolved owner")
        elif owner_reference_index(player) != 0:
            raise ValueError("Target-owner reveal requires the owner of its one permanent target")
        return cls(player)


def bind_target_permanent_shuffle_reveal_owner(host, item, effects):
    """Capture the owner while the revalidated target incarnation still exists."""
    if not any(effect.get("op") == WARP_OPERATION for effect in effects):
        return effects
    if (
        len(effects) != 2
        or effects[0] != {"op": "shuffle_into_library", "card": "$target.0"}
        or item.context.get("targets_revalidated") is not True
    ):
        raise GameRuleError("Target-owner shuffle and reveal requires its two complete resolving instructions")
    try:
        spec = TargetPermanentShuffleRevealSpec.from_effect(effects[1])
        owner = resolve_target_owner_information(host, spec.player, item)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    if owner is None:
        raise GameRuleError("Target-owner shuffle and reveal requires a revalidated permanent owner")
    return [effects[0], {**effects[1], "player": owner}]


def bound_shuffle_reveal_owner(effect, *, stack_ref, controller):
    binding = effect.get(WARP_OWNER_BINDING)
    if (
        not isinstance(binding, Mapping)
        or set(binding) != {"schema_version", "stack_ref", "controller", "owner"}
        or type(binding["schema_version"]) is not int
        or binding["schema_version"] != 1
        or binding["stack_ref"] != stack_ref
        or binding["controller"] != controller
        or type(binding["owner"]) is not str
        or not binding["owner"]
        or binding["owner"] != effect.get("player")
    ):
        raise ValueError("Shuffled library result requires the owner bound by its resolving stack object")
    return binding["owner"]
