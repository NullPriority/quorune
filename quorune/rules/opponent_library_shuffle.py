"""Observe committed opponent-library shuffles and own their bounded private result."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


LIBRARY_SHUFFLED_EVENT = "library.shuffled"
OPPONENT_LIBRARY_SHUFFLED_CAPABILITY = "trigger.event.opponent_library_shuffled"
OPPONENT_SHUFFLE_TOP_PARTITION_CAPABILITY = "library.select.opponent_shuffle_top_partition"
OPPONENT_SHUFFLE_TOP_PARTITION_OPERATION = "inspect_opponent_shuffled_library_top"
OPPONENT_SHUFFLE_CAPABILITIES = (
    OPPONENT_LIBRARY_SHUFFLED_CAPABILITY, OPPONENT_SHUFFLE_TOP_PARTITION_CAPABILITY,
)
OPPONENT_SHUFFLE_CONDITION = FrozenMap({"field": "player", "op": "ne", "value": "$source.controller"})


def opponent_shuffle_top_effect():
    return {"op": OPPONENT_SHUFFLE_TOP_PARTITION_OPERATION, "schema_version": 1,
            "player": "$controller", "library_owner": "$context.player",
            "shuffle_count": "$context.shuffle_count"}


def opponent_shuffle_runtime_dependencies(program):
    marked = any(value.get("op") == OPPONENT_SHUFFLE_TOP_PARTITION_OPERATION
                 for value in nested_effects((program.effects, program.handlers)))
    if not (marked or program.event == LIBRARY_SHUFFLED_EVENT
            or set(OPPONENT_SHUFFLE_CAPABILITIES).intersection(program.capability_dependencies)):
        return ()
    if ((program.event, program.active_zone) != (LIBRARY_SHUFFLED_EVENT, "battlefield")
            or not program.ability_id.startswith("trigger:") or program.target_schema is not None
            or program.cost_schema is not None or program.handlers
            or program.event_condition != OPPONENT_SHUFFLE_CONDITION
            or tuple(program.effects) != (opponent_shuffle_top_effect(),)):
        raise ValueError("An opponent shuffle partition requires its complete original observer")
    return OPPONENT_SHUFFLE_CAPABILITIES


def dispatch_committed_library_shuffle(host, player, count):
    from .event_subscriptions import event_has_capability_subscriber

    if event_has_capability_subscriber(host, LIBRARY_SHUFFLED_EVENT, OPPONENT_LIBRARY_SHUFFLED_CAPABILITY):
        host._dispatch_semantic_event(LIBRARY_SHUFFLED_EVENT, {"player": player, "shuffle_count": count})


def bound_opponent_shuffle_owner(effect, actor, *, extra=()):
    if (not isinstance(effect, Mapping) or set(effect) != set(opponent_shuffle_top_effect()).union(extra)
            or effect["op"] != OPPONENT_SHUFFLE_TOP_PARTITION_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
            or effect["player"] != actor
            or type(effect["library_owner"]) is not str or not effect["library_owner"]
            or effect["library_owner"] == actor
            or type(effect["shuffle_count"]) is not int or effect["shuffle_count"] < 1):
        raise ValueError("The private partition requires its original opponent shuffle and controller")
    return effect["library_owner"]


def admitted_opponent_shuffle_scope(host, item):
    """Expose the registered, resolving trigger’s public occurrence."""
    owner = item.context.get("player")
    count = item.context.get("shuffle_count")
    if (item.kind != "triggered_ability" or item.targets
            or item.context.get("currently_resolving") is not True
            or item.context.get("event") != LIBRARY_SHUFFLED_EVENT
            or type(owner) is not str or not owner or owner == item.controller
            or type(count) is not int or count < 1):
        return None
    program = host.semantics.get(item.semantic_key)
    if (program is None or owner not in host.state.players or not host.semantic_program_is_current_trusted(program)
            or opponent_shuffle_runtime_dependencies(program) != OPPONENT_SHUFFLE_CAPABILITIES):
        return None
    return FrozenMap({"player": owner, "shuffle_count": count,
                      "library_size": len(host.state.players[owner].zones["library"])})


def scoped_opponent_shuffle_library(host, item, actor, effect):
    """Materialize only the authorized current top after the controller chose to look."""
    if (effect.get("op") != OPPONENT_SHUFFLE_TOP_PARTITION_OPERATION or item is None
            or item.controller != actor or effect.get("_accepted") is not True):
        return {}
    scope = admitted_opponent_shuffle_scope(host, item)
    extra = ("_accepted", "_looked") if "_looked" in effect else ("_accepted",)
    owner = bound_opponent_shuffle_owner(effect, actor, extra=extra)
    if (scope is None or scope["player"] != owner or scope["shuffle_count"] != effect["shuffle_count"]
            or owner not in host.active_seats):
        return {}
    library = host.state.players[owner].zones["library"]
    return {owner: tuple(host.state.cards[oid].ref for oid in library[-2:])}


def validate_opponent_shuffle_partition_commit(
    host, *, actor, player, arrangement, source_stack_ref, looked_are_public, selected_are_public,
):
    """Authorize foreign ordering against the still-resolving, admitted trigger."""
    item = next((value for value in host.state.stack if value.ref == source_stack_ref), None)
    occurrence = None if item is None else admitted_opponent_shuffle_scope(host, item)
    if occurrence is None or item.controller != actor or occurrence["player"] != player:
        raise ValueError("Foreign library order requires its original resolving shuffle trigger")
    library = host.state.players[player].zones["library"]
    if (looked_are_public or selected_are_public
            or arrangement.remainder_order != "chosen" or len(arrangement.remainder_refs) > 1
            or len(arrangement.looked) != min(2, len(library))):
        raise ValueError("The committed private shuffle partition must use its full top and at most one exile")
