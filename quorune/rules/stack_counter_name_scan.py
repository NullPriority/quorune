"""Counter a spell, then offer its controller the first different nonland name."""

from collections.abc import Mapping

from ..carddb_characteristics import public_stack_rules_names
from ..replacement.immutable import FrozenMap
from ..stack_spell_name_scan_model import NAME_SCAN_CAPABILITY, NAME_SCAN_MECHANIC, StackSpellNameScan
from .random_selection import choose_random_integer


COUNTER_NAME_SCAN_OPERATION = "resolve_stack_counter_name_scan"
COUNTER_NAME_SCAN_EFFECT = FrozenMap({
    "op": COUNTER_NAME_SCAN_OPERATION,
    "schema_version": 1,
    "stack": "$target.0",
})
COUNTER_NAME_SCAN_TARGET = FrozenMap({
    "zones": ["stack"], "categories": ["spell"], "source_exclusion": True, "count": 1,
})
COUNTER_NAME_SCAN_MECHANICS = ("counter", "cr-115-targets", "mill", NAME_SCAN_MECHANIC)


def counter_name_scan_node_capabilities(effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or FrozenMap(effects[0]) != COUNTER_NAME_SCAN_EFFECT
            or type(effects[0].get("schema_version")) is not int
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != COUNTER_NAME_SCAN_TARGET
            or not set(COUNTER_NAME_SCAN_MECHANICS).issubset(mechanic_ids)):
        return ()
    return (NAME_SCAN_CAPABILITY, "stack.counter.effect", "target.revalidate_resolution", "zone.mill.fixed",
            "casting.permission.one_shot_exile_without_mana")


def bind_counter_name_scan(host, item, effect, *, target_ref):
    if (FrozenMap(effect) != COUNTER_NAME_SCAN_EFFECT or type(effect["schema_version"]) is not int
            or item.kind not in {"spell", "spell_copy"} or item.context.get("currently_resolving") is not True):
        raise ValueError("The different-name exile needs the original resolving counter spell")
    target = next((value for value in host.state.stack if value.ref == target_ref), None)
    if target is None or target.kind not in {"spell", "spell_copy"} or tuple(item.targets) != (target_ref,):
        raise ValueError("The different-name exile needs its revalidated spell target")
    card = host.state.cards.get(target.card_object_id)
    if card is None or card.zone != "stack":
        raise ValueError("The target spell lost its current physical or copied object")
    names = public_stack_rules_names(card, host._effective_card_data(card), host.card_record(card))
    scan = StackSpellNameScan(item.ref, target_ref, target.controller, tuple(sorted(set(names))))
    count = choose_random_integer(
        1, 3, seed=host.state.config.seed, instruction_key=item.stack_id, seat=target.controller,
    )
    return {"op": COUNTER_NAME_SCAN_OPERATION, "schema_version": 1, "scan": scan.to_dict(), "mill_count": count}


def counter_name_scan_instructions(effect, *, active_seats):
    if (set(effect) != {"op", "schema_version", "scan", "mill_count"} or effect["op"] != COUNTER_NAME_SCAN_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
            or type(effect["mill_count"]) is not int or not 1 <= effect["mill_count"] <= 3):
        raise ValueError("The ordered counter result needs the bound spell, player and random amount")
    scan = StackSpellNameScan.from_dict(effect["scan"])
    counter = {"op": "counter_stack_target", "stack": scan.target_ref}
    if scan.actor not in active_seats:
        return (counter,)
    mill = {"op": "mill", "player": scan.actor, "count": effect["mill_count"]}
    return counter, mill, scan.effect()
