"""The original damage target receives the actual committed sacrifice count."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from ..selection.target_query import target_identity_matches_snapshot
from .committed_quantity_followups import CommittedQuantityFollowup


COMMITTED_SACRIFICE_DAMAGE_CAPABILITY = "damage.amount.committed_sacrifice_count"
_DAMAGE_PREDS = {
    "mountains": "player_or_planeswalker",
    "creatures": "damageable",
}
_RECEIVER_TYPES = {
    "player_or_planeswalker": frozenset({"planeswalker"}),
    "damageable": frozenset({"creature", "planeswalker", "battle"}),
}


def committed_sacrifice_damage_recipe(domain):
    return {"op": "damage", "player": "$controller", "amount": 1, "recipient": _DAMAGE_PREDS[domain]}


def committed_sacrifice_damage_recipe_parts(value, *, actor="$controller"):
    if (not isinstance(value, Mapping) or value.get("op") != "damage"
            or value.get("player") != actor or type(value.get("amount")) is not int):
        raise ValueError("Committed sacrifice damage requires its resolving controller and fixed coefficient")
    recipient = value.get("recipient")
    if type(recipient) is not str or recipient not in _RECEIVER_TYPES:
        raise ValueError("Committed sacrifice damage requires its printed recipient")
    expected = {"op": "damage", "player": actor, "amount": 1, "recipient": recipient}
    if FrozenMap(value) != FrozenMap(expected):
        raise ValueError("Committed sacrifice damage requires one exact target recipe")
    return FrozenMap({**expected, "player": "$controller"})


def committed_sacrifice_damage_domain_matches(domain, recipe):
    return _DAMAGE_PREDS.get(domain) == recipe["recipient"]


def committed_sacrifice_damage_target_schema(recipe):
    return {"zones": ["player", "battlefield"], "categories": ["player", "permanent"],
            "predicate": recipe["recipient"], "count": 1}


def _damage_instruction(count):
    return FrozenMap({"op": "damage", "source": "$source", "target": "$target.0", "amount": count})


def committed_sacrifice_damage_dependencies(recipe, target_schema):
    from .damage_capability_shapes import fixed_damage_node_capabilities

    if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != FrozenMap(committed_sacrifice_damage_target_schema(recipe))):
        raise ValueError("Committed sacrifice damage requires its actual announced target shape")
    owned = fixed_damage_node_capabilities(
        effects=(dict(_damage_instruction(1)),), target_schema=target_schema,
        mechanic_ids=("cr-120-damage", "cr-115-targets"),
    )
    if not owned:
        raise ValueError("Committed sacrifice damage requires the canonical targeted damage owner")
    return (COMMITTED_SACRIFICE_DAMAGE_CAPABILITY, *owned)


def capture_committed_sacrifice_damage_target(host, item):
    if (item is None or item.kind not in {"spell", "spell_copy"} or item.controller not in host.active_seats
            or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True or len(item.targets) != 1):
        raise GameRuleError("Committed sacrifice damage requires a validated original target")
    ref, = item.targets
    if ref in host.active_seats:
        return FrozenMap({"kind": "player", "ref": ref})
    snapshots = item.context.get("target_snapshots")
    snapshot = snapshots.get(ref) if isinstance(snapshots, Mapping) else None
    if (not isinstance(snapshot, Mapping) or type(snapshot.get("object_id")) is not str
            or type(snapshot.get("zone_change_counter")) is not int
            or not target_identity_matches_snapshot(host, ref, snapshot)):
        raise GameRuleError("Committed sacrifice damage lost the original public target")
    card = host.state.cards.get(snapshot["object_id"])
    if card is None or card.zone != "battlefield" or card.phased_out:
        raise GameRuleError("Committed sacrifice damage requires its live public permanent target")
    return FrozenMap({"kind": "permanent", "ref": ref, "object_id": card.object_id,
                      "logical_object_id": card.logical_object_id})


def committed_sacrifice_damage_target(value):
    if (not isinstance(value, Mapping) or value.get("kind") not in ("player", "permanent")
            or type(value.get("ref")) is not str or not value["ref"] or value["ref"].startswith("$")):
        raise ValueError("Committed sacrifice damage requires a sealed ordinary target")
    expected = {"kind", "ref"}
    if value["kind"] == "permanent":
        expected |= {"object_id", "logical_object_id"}
        if (type(value.get("object_id")) is not str or not value["object_id"]
                or type(value.get("logical_object_id")) is not str
                or value["logical_object_id"].rpartition("@")[0] != value["object_id"]):
            raise ValueError("Committed sacrifice damage requires the original target incarnation")
    if set(value) != expected:
        raise ValueError("Committed sacrifice damage target fields are closed")
    return FrozenMap(value)


def _target_is_still_damageable(host, recipient, target):
    if target["kind"] == "player":
        return target["ref"] in host.active_seats
    card = host.state.cards.get(target["object_id"])
    if (card is None or card.ref != target["ref"] or card.logical_object_id != target["logical_object_id"]
            or card.zone != "battlefield" or card.phased_out):
        return False
    types = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))[0]
    return bool(_RECEIVER_TYPES[recipient].intersection(types))


def committed_sacrifice_damage_followup(host, recipe, target, count):
    if type(count) is not int or count < 0:
        raise ValueError("Committed sacrifice damage count is malformed")
    if not count or not _target_is_still_damageable(host, recipe["recipient"], target):
        return CommittedQuantityFollowup(())
    return CommittedQuantityFollowup((_damage_instruction(count),))
