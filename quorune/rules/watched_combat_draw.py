"""A turn-bounded delayed draw for one watched creature incarnation."""

from collections.abc import Mapping

from ..delayed_combat_draw_model import WATCHED_COMBAT_DRAW_FIELD
from ..replacement.immutable import FrozenMap
from ..selection.target_query import target_identity_matches_snapshot
from ..target_source import capture_targeting_source
from .effect_shape_walkers import contains_nested_key


WATCHED_COMBAT_DRAW_CAPABILITY = "trigger.delayed.watched_combat_damage_draw"
WATCHED_COMBAT_DRAW_MECHANIC = "watched-combat-damage-draw"
WATCHED_COMBAT_DRAW_LABEL = "Draw for the chosen creature's combat damage"
WATCHED_COMBAT_DRAW_TARGET = {
    "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
    "controller_relation": "you", "count": 1,
}
WATCHED_COMBAT_DRAW_DEPENDENCIES = (
    WATCHED_COMBAT_DRAW_CAPABILITY, "trigger.event.normalized_damage", "trigger.placement.apnap",
    "target.permanent.characteristic_predicate", "target.revalidate_resolution", "zone.draw.library_to_hand",
)


def watched_combat_draw_effect():
    return {"op": "delayed_trigger", WATCHED_COMBAT_DRAW_FIELD: 1, "card": "$target.0"}


def watched_combat_draw_node_dependencies(effects, schema):
    if (len(effects) != 1 or not isinstance(schema, Mapping) or type(schema.get("count")) is not int
            or FrozenMap(schema) != FrozenMap(WATCHED_COMBAT_DRAW_TARGET)):
        return ()
    effect, = effects
    return WATCHED_COMBAT_DRAW_DEPENDENCIES if (
        type(effect.get(WATCHED_COMBAT_DRAW_FIELD)) is int and dict(effect) == watched_combat_draw_effect()
    ) else ()


def watched_combat_draw_program_dependencies(program):
    if not (contains_nested_key((program.effects, program.handlers), WATCHED_COMBAT_DRAW_FIELD)
            or WATCHED_COMBAT_DRAW_CAPABILITY in program.capability_dependencies):
        return ()
    required = watched_combat_draw_node_dependencies(program.effects, program.target_schema)
    if (
        not required or (program.event, program.active_zone) != ("resolve", "stack")
        or program.handlers or program.cost_schema is not None or program.event_condition is not None
    ):
        raise ValueError("Watched combat damage requires the complete original targeted spell")
    return required


def is_closed_watched_combat_draw_program(program):
    try:
        required = watched_combat_draw_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def bind_watched_combat_draw(host, item, raw):
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    if (
        type(raw.get(WATCHED_COMBAT_DRAW_FIELD)) is not int or dict(raw) != watched_combat_draw_effect()
        or item.kind not in {"spell", "spell_copy"} or item.context.get("currently_resolving") is not True
        or program is None or not host.semantic_program_is_current_trusted(program)
        or not is_closed_watched_combat_draw_program(program)
        or len(item.targets) != 1
    ):
        raise ValueError("The combat draw lost its original resolving spell")
    ref, = item.targets
    snapshot = item.context.get("target_snapshots", {}).get(ref)
    if not ref or not isinstance(snapshot, Mapping) or not target_identity_matches_snapshot(host, ref, snapshot):
        return None
    watched = host.state.cards[snapshot["object_id"]]
    spell = host.state.cards.get(item.card_object_id or "")
    if spell is None or spell.zone != "stack" or spell.controller != item.controller:
        raise ValueError("The delayed trigger source must be its resolving spell or copy")
    return {
        "op": "delayed_trigger", "controller": item.controller, "label": WATCHED_COMBAT_DRAW_LABEL,
        "event": "damage.dealt", "condition": {
            WATCHED_COMBAT_DRAW_FIELD: 1, "combat": True,
            "source_object_id": watched.object_id, "source_logical_object_id": watched.logical_object_id,
        },
        "stack": {"label": WATCHED_COMBAT_DRAW_LABEL}, "once": False,
        "expires_turn_sequence": host.state.turn_sequence,
        "source_binding": capture_targeting_source(host, spell).to_dict(),
    }


def watched_combat_draw_amount(context):
    amount = context.get("amount")
    kind = context.get("target_kind")
    target = context.get("target")
    types = context.get("target_types")
    if (
        context.get("combat") is not True or type(amount) is not int or amount <= 0
        or type(target) is not str or not target
        or not ((kind == "player" and context.get("player") == target)
                or (kind == "permanent" and isinstance(types, (list, tuple)) and "planeswalker" in types))
    ):
        return None
    return amount

def validate_bound_watched_combat_draw(host, effect, actor):
    context = effect.get("source_binding")
    object_id = context.get("object_id") if isinstance(context, Mapping) else None
    item = next((item for item in host.state.stack if object_id and item.card_object_id == object_id), None)
    if item is None or item.controller != actor:
        raise ValueError("Watched combat damage must be scheduled by its originating spell")
    expected = bind_watched_combat_draw(host, item, watched_combat_draw_effect())
    actual = {key: value for key, value in effect.items() if key not in {"reason", "_runtime_source"}}
    if expected is None or FrozenMap(actual) != FrozenMap(expected):
        raise ValueError("The delayed combat damage watch lost its current target or duration")
