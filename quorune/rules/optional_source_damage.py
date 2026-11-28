"""Closed announced-player optional source damage decisions."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..random_discard import RANDOM_DISCARD_OPERATION
from ..replacement.immutable import FrozenMap, thaw_value
from ..target_controller_reference import TARGET_CONTROLLER_INFORMATION_CAPABILITY, target_controller_reference
from .committed_mill_cards import MilledManaDamage, PUBLIC_MILLED_CARDS_CAPABILITY, uses_milled_mana_damage
from .effect_shape_walkers import contains_nested_key, nested_effects


OPTIONAL_SOURCE_DAMAGE_FIELD = "optional_source_damage"
OPTIONAL_SOURCE_DAMAGE_MECHANIC = "optional-source-damage-alternate"
OPTIONAL_SOURCE_DAMAGE_CAPABILITY = "resolution.choice.optional_source_damage"
OPTIONAL_LAND_CONTROLLER_DAMAGE_CAPABILITY = "resolution.choice.optional_land_controller_damage"
OPTIONAL_TARGET_CONTROLLER_DAMAGE_CAPABILITY = "resolution.choice.optional_target_controller_damage"
OPTIONAL_GRANTED_CREATURE_DAMAGE_CAPABILITY = "resolution.choice.optional_granted_creature_damage"
OPTIONAL_GRANTED_CREATURE_DAMAGE_TEMPLATE = "temporary-received-damage-grant-with-owner-choice-v1"
_PROMPT = "Allow this spell to deal damage to you?"
_BRANCHES = ("accept_damage", "decline_damage")
MILLED_MANA_CHOICE_VERSION = 2
_MILLED_MANA_BRANCHES = ("allow_draw", "mill_then_damage")
_OPPONENT_SCHEMA = {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "opponent"}
_FIELDS = {"op", OPTIONAL_SOURCE_DAMAGE_FIELD, "player", "prompt", "options", "then_by_choice"}
_CONTINUATION_FIELDS = {"_choice_actor", "_source_ref", "_legal_values", "_stack_label"}
_NONBASIC_LAND_TARGET = {
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1,
    "types_any": ["land"], "supertypes_none": ["basic"],
}

_GRANTED_CREATURE_TARGET = {
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"],
}


def optional_granted_creature_damage(amount):
    damage = {"op": "damage", "source": "$source", "target": "$target.0", "amount": amount}
    return {
        "op": "choose_option", OPTIONAL_SOURCE_DAMAGE_FIELD: 2,
        "player": target_controller_reference(),
        "prompt": "Allow the source to deal damage to the targeted creature?",
        "options": [
            {"id": "accept_damage", "label": f"Allow {amount} damage"},
            {"id": "decline_damage", "label": "Decline the damage"},
        ],
        "then_by_choice": {"accept_damage": [damage], "decline_damage": []},
    }


def _granted_creature_damage_capabilities(effects, target_schema, mechanic_ids):
    from .damage_capability_shapes import fixed_damage_node_capabilities
    from .temporary_ability_grant import temporary_quoted_grant_node_capabilities

    if thaw_value(target_schema) != _GRANTED_CREATURE_TARGET or type(target_schema.get("count")) is not int:
        return ()
    grant, choice = thaw_value(tuple(effects))
    branches = choice.get("then_by_choice")
    if not isinstance(branches, Mapping):
        return ()
    accepted = branches.get("accept_damage")
    if not isinstance(accepted, list) or len(accepted) != 1 or not isinstance(accepted[0], Mapping):
        return ()
    amount = accepted[0].get("amount")
    if type(amount) is not int or amount <= 0 or choice != optional_granted_creature_damage(amount):
        return ()
    grant_deps = temporary_quoted_grant_node_capabilities(
        effects=(grant,), target_schema=target_schema, mechanic_ids=mechanic_ids,
    )
    if not grant_deps:
        return ()
    fragment, = grant["ability_fragments"]
    if fragment["kind"] != "granted_triggered" or fragment["value"]["event"] != "damage.received":
        return ()
    damage_deps = fixed_damage_node_capabilities(
        effects=tuple(accepted), target_schema=target_schema, mechanic_ids=mechanic_ids,
    )
    if not damage_deps:
        return ()
    return tuple(sorted({
        *grant_deps, *damage_deps, OPTIONAL_SOURCE_DAMAGE_CAPABILITY,
        OPTIONAL_GRANTED_CREATURE_DAMAGE_CAPABILITY, TARGET_CONTROLLER_INFORMATION_CAPABILITY,
    }))


def source_damage_choice(damage, alternate, *, chooser="$target.0"):
    if alternate["op"] == "destroy":
        fallback = "Destroy the targeted land"
    elif alternate["op"] == "damage":
        fallback = f"Deal {alternate['amount']} damage to the targeted creature"
    elif alternate["op"] == "counter_stack_target":
        fallback = "Counter the targeted spell"
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


def milled_mana_damage_choice(draw_count, mill_count):
    return {
        "op": "choose_option", OPTIONAL_SOURCE_DAMAGE_FIELD: MILLED_MANA_CHOICE_VERSION, "player": "$target.0",
        "prompt": "Allow the controller to draw cards, or have the source deal damage to you?",
        "options": [
            {"id": "allow_draw", "label": f"The controller draws {draw_count} cards"},
            {"id": "mill_then_damage", "label": f"The controller mills {mill_count} cards and the source deals damage"},
        ],
        "then_by_choice": {
            "allow_draw": [{"op": "draw", "player": "$controller", "count": draw_count, "private": True}],
            "mill_then_damage": [{
                "op": "mill", "player": "$controller", "count": mill_count,
                "followup": MilledManaDamage("$target.0").to_dict(),
            }],
        },
    }


def milled_mana_damage_target_schema():
    return {**_OPPONENT_SCHEMA, "zones": ["player"], "categories": ["player"]}


def _milled_mana_choice_capabilities(choice, schema, mechanic_ids):
    from .damage_capability_shapes import fixed_damage_node_capabilities
    from .mill_capability_shapes import fixed_mill_node_capabilities
    from .node_capability_shapes import fixed_draw_node_capabilities

    if schema != _OPPONENT_SCHEMA or type(schema["count"]) is not int:
        return ()
    try:
        draw, = choice["then_by_choice"]["allow_draw"]
        mill, = choice["then_by_choice"]["mill_then_damage"]
        draw_count, mill_count = draw["count"], mill["count"]
    except (KeyError, TypeError, ValueError):
        return ()
    if (type(draw_count) is not int or draw_count <= 0 or type(mill_count) is not int or mill_count <= 0
            or choice != milled_mana_damage_choice(draw_count, mill_count)):
        return ()
    draw_owners = fixed_draw_node_capabilities(effects=(draw,), target_schema=None, mechanic_ids=mechanic_ids)
    mill_owners = fixed_mill_node_capabilities(
        effects=({"op": "mill", "player": "$controller", "count": mill_count},),
        target_schema=None, mechanic_ids=mechanic_ids,
    )
    damage_owners = fixed_damage_node_capabilities(
        effects=({"op": "damage", "source": "$source", "target": "$target.0", "amount": 1},),
        target_schema=schema, mechanic_ids=mechanic_ids,
    )
    if not draw_owners or not mill_owners or not damage_owners:
        return ()
    return tuple(sorted({
        OPTIONAL_SOURCE_DAMAGE_CAPABILITY, PUBLIC_MILLED_CARDS_CAPABILITY, "target.revalidate_resolution",
        "trigger.event.normalized_zone_change", "trigger.placement.apnap", *draw_owners, *mill_owners, *damage_owners,
    }))


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


def _target_controller_damage_capabilities(choice, damage, alternate, schema, mechanic_ids):
    from .damage_capability_shapes import fixed_damage_node_capabilities
    from .node_capability_shapes import targeted_counter_node_capabilities
    from .stack_counter_followup import STACK_COUNTER_FOLLOWUP_CAPABILITY, STACK_TARGET_CONTROLLER

    amount = damage.get("amount")
    if type(amount) is not int or amount <= 0 or not isinstance(schema, dict) or type(schema.get("count")) is not int:
        return ()
    if alternate.get("op") == "damage":
        if schema != {"zones": ["battlefield"], "categories": ["permanent"],
                      "count": 1, "types_any": ["creature"]}:
            return ()
        chooser = target_controller_reference()
        received = damage
        fallback = fixed_damage_node_capabilities(effects=(alternate,), target_schema=schema, mechanic_ids=mechanic_ids)
        support = ()
    else:
        if (schema != {"zones": ["stack"], "categories": ["spell"], "source_exclusion": True,
                       "count": 1, "types_any": ["instant", "sorcery"]}
                or alternate != {"op": "counter_stack_target", "stack": "$target.0"}):
            return ()
        chooser = STACK_TARGET_CONTROLLER
        received = {**damage, "target": "$controller"}
        fallback = targeted_counter_node_capabilities(effects=(alternate,), target_schema=schema, mechanic_ids=mechanic_ids)
        support = (STACK_COUNTER_FOLLOWUP_CAPABILITY,)
    if (damage != {"op": "damage", "source": "$source", "target": chooser, "amount": amount}
            or not fallback or choice != source_damage_choice(damage, alternate, chooser=chooser)):
        return ()
    accepted = fixed_damage_node_capabilities(effects=(received,), target_schema=None, mechanic_ids=mechanic_ids)
    return tuple(sorted({OPTIONAL_SOURCE_DAMAGE_CAPABILITY, OPTIONAL_TARGET_CONTROLLER_DAMAGE_CAPABILITY,
                         "target.revalidate_resolution", *accepted, *fallback, *support})) if accepted else ()


def optional_source_damage_node_capabilities(*, effects, target_schema, mechanic_ids):
    from .damage_capability_shapes import fixed_damage_node_capabilities
    from .node_capability_shapes import fixed_draw_node_capabilities
    from ..random_discard import random_discard_node_capabilities

    if OPTIONAL_SOURCE_DAMAGE_MECHANIC not in mechanic_ids:
        return ()
    if len(effects) == 2 and effects[1].get(OPTIONAL_SOURCE_DAMAGE_FIELD) == 2:
        return _granted_creature_damage_capabilities(effects, target_schema, mechanic_ids)
    if len(effects) != 1:
        return ()
    choice = thaw_value(FrozenMap(effects[0]))
    schema = thaw_value(FrozenMap(target_schema)) if isinstance(target_schema, Mapping) else None
    if set(choice) != _FIELDS or type(choice[OPTIONAL_SOURCE_DAMAGE_FIELD]) is not int:
        return ()
    if choice[OPTIONAL_SOURCE_DAMAGE_FIELD] == MILLED_MANA_CHOICE_VERSION:
        return _milled_mana_choice_capabilities(choice, schema, mechanic_ids)
    if choice[OPTIONAL_SOURCE_DAMAGE_FIELD] != 1:
        return ()
    branches = choice["then_by_choice"]
    if (not isinstance(branches, dict) or set(branches) != set(_BRANCHES)
            or any(not isinstance(row, list) or len(row) != 1 or not isinstance(row[0], dict)
                   for row in branches.values())):
        return ()
    damage = branches["accept_damage"][0]
    alternate = branches["decline_damage"][0]
    if alternate.get("op") in {"damage", "counter_stack_target"}:
        return _target_controller_damage_capabilities(choice, damage, alternate, schema, mechanic_ids)
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
    instructions = (*program.effects, *getattr(program, "handlers", ()))
    if not (
        contains_nested_key(instructions, OPTIONAL_SOURCE_DAMAGE_FIELD)
        or any(uses_milled_mana_damage(effect) for effect in nested_effects(instructions))
    ):
        return ()
    result = optional_source_damage_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not result:
        raise ValueError("Optional source damage requires its exact choice")
    granted = OPTIONAL_GRANTED_CREATURE_DAMAGE_CAPABILITY in result
    expected_event = ("activate", "battlefield") if granted else ("resolve", "stack")
    if not granted and program.effects[-1][OPTIONAL_SOURCE_DAMAGE_FIELD] == MILLED_MANA_CHOICE_VERSION:
        if ((program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
                or not program.ability_id.startswith("trigger:") or program.handlers
                or program.cost_schema is not None or program.event_condition is not None):
            raise ValueError("Milled mana-value damage requires its complete source-entry trigger")
    elif (program.event, program.active_zone) != expected_event:
        raise ValueError("Optional source damage requires the complete original spell resolution")
    if granted and (program.provenance.get("template_id") != OPTIONAL_GRANTED_CREATURE_DAMAGE_TEMPLATE
                    or (program.cost_schema or {}).get("tap_source") is not True):
        raise ValueError("Optional damage to a granted creature requires its original tap activation")
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
    if (not item.context.get("currently_resolving") or program is None
            or not host.semantic_program_is_current_trusted(program)):
        raise GameRuleError("Optional damage requires its current trusted resolving source")
    try:
        required = optional_source_damage_runtime_dependencies(program)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError("Optional damage requires its current trusted resolving source") from exc
    version = effect[OPTIONAL_SOURCE_DAMAGE_FIELD]
    if (OPTIONAL_SOURCE_DAMAGE_CAPABILITY not in required or type(version) is not int
            or version != program.effects[-1][OPTIONAL_SOURCE_DAMAGE_FIELD]):
        raise GameRuleError("Optional damage requires its current trusted resolving source")
    granted = OPTIONAL_GRANTED_CREATURE_DAMAGE_CAPABILITY in required
    mill_choice = not granted and version == MILLED_MANA_CHOICE_VERSION
    if mill_choice:
        if (item.kind not in {"triggered_ability", "triggered_ability_copy"}
                or item.context.get("targets_revalidated") is not True
                or tuple(item.targets) != (effect["player"],) or effect["player"] == item.controller):
            raise GameRuleError("Milled mana-value damage requires the trigger's original legal opponent")
    elif item.kind not in ({"activated_ability"} if granted else {"spell", "spell_copy"}):
        raise GameRuleError("Optional fixed damage requires its resolving spell")
    supplied = thaw_value(FrozenMap(effect))
    if resuming_actor is not None:
        branches = _MILLED_MANA_BRANCHES if mill_choice else _BRANCHES
        if (supplied.get("_choice_actor") != resuming_actor or supplied.get("player") != resuming_actor
                or supplied.get("_legal_values") != list(branches)):
            raise GameRuleError("The optional-damage choice lost its original payer or outcomes")
        supplied = {key: value for key, value in supplied.items() if key not in _CONTINUATION_FIELDS}
    expected = thaw_value(FrozenMap(host._semantic_value(program.effects[-1], item)))
    if supplied != expected or supplied["player"] not in host.active_seats:
        raise GameRuleError("The optional-damage choice changed its original target or outcomes")
