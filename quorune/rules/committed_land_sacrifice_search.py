"""Exact private tapped-land search bound to a resolving land sacrifice."""

from collections.abc import Mapping

from ..committed_land_search_model import (
    COMMITTED_LAND_SEARCH_CAPABILITY, COMMITTED_LAND_SEARCH_FIELD, COMMITTED_LAND_SEARCH_VERSION,
    land_sacrifice_search_recipe, validate_land_sacrifice_search_recipe,
)
from ..replacement.immutable import FrozenMap


def _resolved_search(player, *, stack_ref, members, maximum):
    effect = land_sacrifice_search_recipe(player=player)
    effect.pop("player")
    return {**effect, "searching_player": player, "count": {"minimum": 0, "maximum": maximum},
            COMMITTED_LAND_SEARCH_FIELD: {"stack_ref": stack_ref, "members": list(members)}}


def validate_resolved_land_sacrifice_search(effect):
    if not isinstance(effect, Mapping) or not isinstance(effect.get("count"), Mapping):
        raise ValueError("Committed land search has no exact count")
    receipt = effect.get(COMMITTED_LAND_SEARCH_FIELD)
    player = effect.get("searching_player")
    maximum = effect["count"].get("maximum")
    if (
        not isinstance(receipt, Mapping) or set(receipt) != {"stack_ref", "members"}
        or type(receipt["stack_ref"]) is not str or not receipt["stack_ref"]
        or type(player) is not str or not player or player.startswith("$")
        or type(maximum) is not int or maximum < 0
        or not isinstance(receipt["members"], (list, tuple))
        or any(type(member) is not str or not member for member in receipt["members"])
        or tuple(receipt["members"]) != tuple(sorted(set(receipt["members"])))
        or maximum > len(receipt["members"])
    ):
        raise ValueError("Committed land search lost its original stack or selected incarnations")
    expected = _resolved_search(player, stack_ref=receipt["stack_ref"], members=receipt["members"], maximum=maximum)
    if (FrozenMap(effect) != FrozenMap(expected) or type(effect["schema_version"]) is not int
            or type(effect["count"]["minimum"]) is not int or effect["enters_tapped_override"] is not True
            or effect["reveal"] is not False or effect["shuffle_after"] is not True):
        raise ValueError("Committed land search has altered its exact result")
    return player, tuple(receipt["members"]), maximum


def _require_original_spell(host, item):
    from .any_number_sacrifice import ANY_NUMBER_SACRIFICE_FIELD, any_number_sacrifice_runtime_dependencies

    program = host.semantics.get(item.semantic_key) if item is not None and item.semantic_key else None
    if (
        item is None or item.kind not in {"spell", "spell_copy"} or item.targets
        or item.context.get("currently_resolving") is not True or program is None
        or not host.semantic_program_is_current_trusted(program)
        or COMMITTED_LAND_SEARCH_CAPABILITY not in any_number_sacrifice_runtime_dependencies(program)
        or len(program.effects) != 1 or ANY_NUMBER_SACRIFICE_FIELD not in program.effects[0]
    ):
        raise ValueError("The committed land search lost its original resolving spell")


def _committed_count(host, player, members):
    return len({event.object_incarnation for event in host._current_turn_history("permanent_sacrificed")
                if event.actor == player and event.object_incarnation in members})


def commit_land_sacrifice_search(host, item, members):
    _require_original_spell(host, item)
    members = tuple(sorted(members))
    return FrozenMap(_resolved_search(
        item.controller, stack_ref=item.ref, members=members,
        maximum=_committed_count(host, item.controller, members),
    ))


def verify_land_sacrifice_search(host, item, effect):
    _require_original_spell(host, item)
    player, members, maximum = validate_resolved_land_sacrifice_search(effect)
    if (player != item.controller or effect[COMMITTED_LAND_SEARCH_FIELD]["stack_ref"] != item.ref
            or maximum != _committed_count(host, player, members)):
        raise ValueError("A private land search must use its committed sacrifice count")
