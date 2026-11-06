from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..fractional_quantity_model import HALF_MILL_CAPABILITY, HALF_MILL_MECHANIC, HALF_QUANTITY_KIND, HalfQuantitySpec
from ..compiler.mill_templates import MILL_CAPABILITY_ID, MILL_MECHANIC_ID,EVENT_MILL_RECIPIENT_CAPABILITY,EVENT_MILL_RECIPIENT_MECHANIC


def _player_target_schema_is_closed(
    target_schema: Mapping[str, object] | None,
) -> bool:
    if not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int:
        return False
    required = {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
    }
    if any(
        target_schema.get(key) != value
        for key, value in required.items()
    ):
        return False
    fields = set(target_schema)
    return fields in (
        set(required),
        {*required, "player_relation"},
    ) and target_schema.get("player_relation", "any") in {
        "any",
        "opponent",
    }


def fixed_mill_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Own one mandatory Mill with a closed count and recipient."""
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if MILL_MECHANIC_ID not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if effect.get("op") != "mill":
        return ()
    count = effect.get("count")
    caps = (MILL_CAPABILITY_ID,)
    if HALF_MILL_MECHANIC in mechanics and isinstance(count, Mapping):
        try:
            HalfQuantitySpec.from_dict(count)
        except (TypeError, ValueError):
            return ()
        caps += (HALF_MILL_CAPABILITY,)
    elif type(count) is not int or count <= 0:
        return ()
    if set(effect) == {"op", "scope", "count"}:
        return caps if target_schema is None and effect.get("scope") in {
            "all_active_players", "controller_opponents",
        } else ()
    if set(effect) != {"op", "player", "count"}:
        return ()
    player = effect.get("player")
    if target_schema is None and EVENT_MILL_RECIPIENT_MECHANIC in mechanics and player in {
            "$context.target", "$context.player", "$context.defending_player"}:
        return (*caps, EVENT_MILL_RECIPIENT_CAPABILITY)
    if target_schema is None and player == "$controller":
        return caps
    if "cr-115-targets" in mechanics and player == "$target.0" and _player_target_schema_is_closed(target_schema):
        return (*caps, "target.revalidate_resolution")
    return ()


def half_mill_payload_dependencies(effect):
    """Require half-mill trust from its serialized amount and recipient."""
    count = effect.get("count")
    if effect.get("op") != "mill" or not isinstance(count, Mapping) or count.get("kind") != HALF_QUANTITY_KIND:
        return ()
    schema = {"zones": ["player"], "categories": ["player"], "count": 1} if effect.get("player") == "$target.0" else None
    owned = fixed_mill_node_capabilities(effects=(effect,), target_schema=schema,
        mechanic_ids=(MILL_MECHANIC_ID, HALF_MILL_MECHANIC, EVENT_MILL_RECIPIENT_MECHANIC, "cr-115-targets"))
    if HALF_MILL_CAPABILITY not in owned:
        raise ValueError("Malformed half-library Mill instruction")
    return owned


def validate_half_mill_program_scope(program):
    from .event_player_result_shapes import _event_player_reference_is_closed

    schema = program.target_schema
    modal = isinstance(schema, Mapping) and isinstance(schema.get("modes"), Mapping)
    pending = [program.effects, schema]
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            if half_mill_payload_dependencies(value):
                player = value.get("player")
                if player == "$target.0" and not (modal or _player_target_schema_is_closed(schema)):
                    raise ValueError("Half-library Mill requires its closed player target")
                if isinstance(player, str) and player.startswith("$context.") and not _event_player_reference_is_closed(program, player):
                    raise ValueError("Half-library Mill requires a sealed event player")
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)


def mill_covered_mechanics(supplied):
    return {mechanic for cap,mechanic in ((MILL_CAPABILITY_ID,MILL_MECHANIC_ID),
        (EVENT_MILL_RECIPIENT_CAPABILITY,EVENT_MILL_RECIPIENT_MECHANIC),
        (HALF_MILL_CAPABILITY,HALF_MILL_MECHANIC)) if cap in supplied}


__all__ = ["fixed_mill_node_capabilities","mill_covered_mechanics"]
