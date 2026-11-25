"""Closed announced-player optional source damage decisions."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..random_discard import RANDOM_DISCARD_OPERATION
from ..replacement.immutable import FrozenMap, thaw_value
from ..target_controller_reference import target_controller_reference
from .effect_shape_walkers import contains_nested_key


OPTIONAL_SOURCE_DAMAGE_FIELD = "optional_source_damage"
OPTIONAL_SOURCE_DAMAGE_MECHANIC = "optional-source-damage-alternate"
OPTIONAL_SOURCE_DAMAGE_CAPABILITY = "resolution.choice.optional_source_damage"
OPTIONAL_LAND_CONTROLLER_DAMAGE_CAPABILITY = "resolution.choice.optional_land_controller_damage"
_PROMPT = "Allow this spell to deal damage to you?"
_BRANCHES = ("accept_damage", "decline_damage")
_FIELDS = {"op", OPTIONAL_SOURCE_DAMAGE_FIELD, "player", "prompt", "options", "then_by_choice"}
_CONTINUATION_FIELDS = {"_choice_actor", "_source_ref", "_legal_values", "_stack_label"}
_NONBASIC_LAND_TARGET = {
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1,
    "types_any": ["land"], "supertypes_none": ["basic"],
}


def source_damage_choice(damage, alternate, *, chooser="$target.0"):
    if alternate["op"] == "destroy":
        fallback = "Destroy the targeted land"
    elif alternate["op"] == "draw":
        fallback = f"The caster draws {alternate['count']} cards"
    else:
        fallback = f"Discard {alternate['count']} cards at random"
    return {
        "op": "choose_option", OPTIONAL_SOURCE_DAMAGE_FIELD: 1, "player": chooser,
        "prompt": _PROMPT,
        "options": [
            {"id": "accept_damage", "label": f"Allow {damage['amount']} damage"},
            {"id": "decline_damage", "label": fallback},
        ],
        "then_by_choice": {"accept_damage": [damage], "decline_damage": [alternate]},
    }


def _target_land_controller_choice_capabilities(choice, damage, alternate, schema, mechanic_ids):
    from .damage_capability_shapes import fixed_damage_node_capabilities
    from .destruction_regeneration_capability_shapes import targeted_destruction_node_capabilities

    controller = target_controller_reference()
    if (schema != _NONBASIC_LAND_TARGET or type(schema.get("count")) is not int
            or alternate != {"op": "destroy", "card": "$target.0"}
            or damage.get("target") != controller
            or choice != source_damage_choice(damage, alternate, chooser=controller)):
        return ()
    accepted = fixed_damage_node_capabilities(effects=(damage,), target_schema=None, mechanic_ids=mechanic_ids)
    declined = targeted_destruction_node_capabilities(
        effects=(alternate,), target_schema=schema, mechanic_ids=mechanic_ids,
    )
    if not accepted or not declined:
        return ()
    return tuple(sorted({OPTIONAL_SOURCE_DAMAGE_CAPABILITY, OPTIONAL_LAND_CONTROLLER_DAMAGE_CAPABILITY, *accepted, *declined}))


def optional_source_damage_node_capabilities(*, effects, target_schema, mechanic_ids):
    from .damage_capability_shapes import fixed_damage_node_capabilities
    from .node_capability_shapes import fixed_draw_node_capabilities
    from ..random_discard import random_discard_node_capabilities

    if len(effects) != 1 or OPTIONAL_SOURCE_DAMAGE_MECHANIC not in mechanic_ids:
        return ()
    choice = thaw_value(FrozenMap(effects[0]))
    schema = thaw_value(FrozenMap(target_schema)) if isinstance(target_schema, Mapping) else None
    if (set(choice) != _FIELDS or type(choice[OPTIONAL_SOURCE_DAMAGE_FIELD]) is not int
            or choice[OPTIONAL_SOURCE_DAMAGE_FIELD] != 1):
        return ()
    branches = choice["then_by_choice"]
    if (not isinstance(branches, dict) or set(branches) != set(_BRANCHES)
            or any(not isinstance(row, list) or len(row) != 1 or not isinstance(row[0], dict)
                   for row in branches.values())):
        return ()
    damage = branches["accept_damage"][0]
    alternate = branches["decline_damage"][0]
    if alternate.get("op") == "destroy":
        if "amount" not in damage:
            return ()
        return _target_land_controller_choice_capabilities(choice, damage, alternate, schema, mechanic_ids)
    amount, count = damage.get("amount"), alternate.get("count")
    if (type(amount) is not int or amount <= 0 or type(count) is not int or count <= 0
            or damage != {"op": "damage", "source": "$source", "target": "$target.0", "amount": amount}):
        return ()
    required_schema = {"zones": ["player"], "categories": ["player"], "count": 1}
    if alternate == {"op": "draw", "player": "$controller", "count": count, "private": True}:
        required_schema["player_relation"] = "opponent"
        other = fixed_draw_node_capabilities(effects=(alternate,), target_schema=None, mechanic_ids=mechanic_ids)
    elif alternate == {"op": RANDOM_DISCARD_OPERATION, "actor": "$controller",
                       "players": ["$target.0"], "count": count}:
        required_schema["player_relation"] = "any"
        other = random_discard_node_capabilities(effects=(alternate,), target_schema=schema, mechanic_ids=mechanic_ids)
    else:
        return ()
    if schema != required_schema or type(schema.get("count")) is not int or choice != source_damage_choice(damage, alternate):
        return ()
    direct_schema = {key: value for key, value in schema.items()
                     if key != "player_relation" or value != "any"}
    fixed = fixed_damage_node_capabilities(effects=(damage,), target_schema=direct_schema, mechanic_ids=mechanic_ids)
    if not fixed or not other:
        return ()
    return tuple(sorted({OPTIONAL_SOURCE_DAMAGE_CAPABILITY, "target.revalidate_resolution", *fixed, *other}))


def optional_source_damage_runtime_dependencies(program):
    if not contains_nested_key((*program.effects, *getattr(program, "handlers", ())), OPTIONAL_SOURCE_DAMAGE_FIELD):
        return ()
    result = optional_source_damage_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not result or (program.event, program.active_zone) != ("resolve", "stack"):
        raise ValueError("Optional source damage requires the complete original spell resolution")
    return result


def is_closed_optional_source_damage_program(program):
    try:
        required = optional_source_damage_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def require_optional_source_damage_choice(host, item, effect, *, resuming_actor=None):
    if OPTIONAL_SOURCE_DAMAGE_FIELD not in effect:
        return
    program = host.semantics.get(item.semantic_key)
    if (item.kind not in {"spell", "spell_copy"} or not item.context.get("currently_resolving") or program is None
            or not host.semantic_program_is_current_trusted(program)
            or OPTIONAL_SOURCE_DAMAGE_CAPABILITY not in optional_source_damage_runtime_dependencies(program)):
        raise GameRuleError("Optional damage requires its current trusted resolving spell")
    supplied = thaw_value(FrozenMap(effect))
    if resuming_actor is not None:
        if (supplied.get("_choice_actor") != resuming_actor or supplied.get("player") != resuming_actor
                or supplied.get("_legal_values") != list(_BRANCHES)):
            raise GameRuleError("The optional-damage choice lost its original payer or outcomes")
        supplied = {key: value for key, value in supplied.items() if key not in _CONTINUATION_FIELDS}
    expected = thaw_value(FrozenMap(host._semantic_value(program.effects[0], item)))
    if supplied != expected or supplied["player"] not in host.active_seats:
        raise GameRuleError("The optional-damage choice changed its original target or outcomes")
