"""Seal each player's spared permanents before a simultaneous sacrifice."""

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec


KEEP_THREE_SACRIFICE_FIELD = "keep_three_then_sacrifice"


def keep_three_sacrifice_effect(*, lands_only: bool, actor: str = "$controller"):
    if type(lands_only) is not bool or type(actor) is not str or not actor:
        raise ValueError("Keep-three sacrifice requires its exact scope and resolving player")
    subject = "lands" if lands_only else "permanents"
    return {
        "op": "choose_cards_apnap", "actor": actor, "players": "all", "zone": "battlefield",
        "predicate": ObjectQuerySpec(zones=("battlefield",),
                                     types_all=("land",) if lands_only else ()).to_dict(),
        "count": 3, "then": "sacrifice", KEEP_THREE_SACRIFICE_FIELD: 1,
        "prompt": f"Choose three {subject} you control to keep.",
    }


def keep_three_sacrifice_scope(effect: Mapping, *, actor: str = "$controller"):
    if (effect.get(KEEP_THREE_SACRIFICE_FIELD) != 1
            or type(effect.get(KEEP_THREE_SACRIFICE_FIELD)) is not int
            or type(effect.get("count")) is not int):
        raise ValueError("Keep-three sacrifice requires the exact version and choice count")
    for lands_only in (False, True):
        if effect == keep_three_sacrifice_effect(lands_only=lands_only, actor=actor):
            return "land" if lands_only else "permanent"
    raise ValueError("Keep-three sacrifice changed the required set or player scope")


def unkept_permanents_in_apnap_order(player_order, offered, kept, current):
    order = tuple(player_order)
    if (not order or len(order) != len(set(order)) or any(type(seat) is not str or not seat for seat in order)
            or any(not isinstance(table, Mapping) or set(table) != set(order)
                   for table in (offered, kept, current))):
        raise ValueError("Keep-three sacrifice requires a complete APNAP round")
    seen = set()
    unkept = []
    for seat in order:
        members = offered[seat]
        chosen = kept[seat]
        if (not isinstance(members, Mapping) or members != current[seat]
                or any(type(ref) is not str or not ref or type(identity) is not str or not identity
                       for ref, identity in members.items())
                or set(members) & seen):
            raise ValueError("A permanent or its current incarnation changed during the choice")
        seen.update(members)
        if (not isinstance(chosen, (list, tuple)) or any(type(ref) is not str for ref in chosen)
                or len(chosen) != min(3, len(members)) or len(chosen) != len(set(chosen))
                or not all(ref in members for ref in chosen)):
            raise ValueError("Each player keeps exactly the available quota from their own set")
        protected = set(chosen)
        unkept.extend(ref for ref in sorted(members) if ref not in protected)
    return tuple(unkept)


KEEP_THREE_SACRIFICE_CAPABILITY = "choice.affected_player.keep_three_then_sacrifice"
KEEP_THREE_OFFERED_FIELD = "keep_three_offered_incarnations"


def keep_three_sacrifice_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or KEEP_THREE_SACRIFICE_FIELD not in effects[0] or target_schema is not None
            or not {"fixed-affected-player-sacrifice", "sacrifice"} <= set(mechanic_ids)):
        return ()
    from ..replacement.immutable import freeze_value, thaw_value

    try:
        keep_three_sacrifice_scope(thaw_value(freeze_value(effects[0])))
    except (TypeError, ValueError):
        return ()
    return (KEEP_THREE_SACRIFICE_CAPABILITY, "choice.affected_player.fixed_sacrifice",
            "zone.change.destination_replacement")


def keep_three_sacrifice_runtime_dependencies(program):
    from .effect_shape_walkers import contains_nested_key
    from ..replacement.immutable import freeze_value, thaw_value

    if not contains_nested_key((*program.effects, *getattr(program, "handlers", ())), KEEP_THREE_SACRIFICE_FIELD):
        return ()
    required = keep_three_sacrifice_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required or program.event_condition is not None or getattr(program, "handlers", ()):
        raise ValueError("A keep-three sacrifice requires one complete untargeted effect")
    scope = keep_three_sacrifice_scope(thaw_value(freeze_value(program.effects[0])))
    if scope == "land":
        if (not program.ability_id.startswith("trigger:") or program.event != "permanent.enter.self"
                or program.active_zone != "battlefield" or program.cost_schema is not None):
            raise ValueError("Keeping three lands requires the original source entry")
        return (*required, "trigger.event.normalized_zone_change", "trigger.placement.apnap")
    if not program.ability_id.startswith("spell:"):
        raise ValueError("Keeping three permanents requires the admitted resolving spell")
    return required
