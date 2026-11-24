"""Collect optional source damage choices before dealing one simultaneous batch."""

from collections.abc import Mapping

from ..affected_permanents import AffectedPermanentSetSpec
from ..announced_damage import AllocatedDamageRecipient
from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from ..source_sacrifice_model import SOURCE_SACRIFICE_MECHANIC, fixed_source_sacrifice_effect
from .destruction_regeneration_capability_shapes import mass_destruction_node_capabilities
from .effect_shape_walkers import contains_nested_key


COLLECTIVE_SOURCE_DAMAGE_FIELD = "collective_source_damage"
COLLECTIVE_SOURCE_DAMAGE_MECHANIC = "collective-optional-source-damage"
COLLECTIVE_SOURCE_DAMAGE_CAPABILITY = "resolution.choice.collective_optional_source_damage"
COLLECTIVE_DECLINE_DESTRUCTION_CAPABILITY = "resolution.choice.collective_optional_damage_destruction"
COLLECTIVE_DAMAGE_RECIPIENTS = "collective_damage_recipients"
ROUND_FIELD = "_collective_damage_round"
_CHOICES = ("accept_damage", "decline_damage")
_CONTINUATION_FIELDS = {"_choice_actor", "_source_ref", "_legal_values", "_stack_label"}
_PLAYER_SCHEMA = {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"}
_EFFECT_FIELDS = {"op", "player", "prompt", "options", COLLECTIVE_SOURCE_DAMAGE_FIELD, "result"}
_ALL_CREATURES = AffectedPermanentSetSpec(ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)))


def collective_source_damage_instruction(amount, audience, result):
    return {
        "op": "choose_option", "player": "$controller", "prompt": "Allow this source to deal damage to you?",
        "options": [
            {"id": "accept_damage", "label": f"Allow {amount} damage"},
            {"id": "decline_damage", "label": "Decline damage"},
        ],
        COLLECTIVE_SOURCE_DAMAGE_FIELD: {"schema_version": 1, "amount": amount, "audience": audience},
        "result": result,
    }


def collective_creature_destruction_capabilities(result, mechanic_ids):
    """Reuse the fixed destruction owner for one untargeted, regeneration-prohibited result."""
    if result.get("set") != _ALL_CREATURES.to_dict() or result.get("regeneration_prohibited") is not True:
        return ()
    required = mass_destruction_node_capabilities(
        effects=(result,), target_schema=None, mechanic_ids=mechanic_ids,
    )
    return (COLLECTIVE_DECLINE_DESTRUCTION_CAPABILITY, *required) if required else ()


def _closed_original(effects, target_schema, mechanic_ids):
    from .mill_capability_shapes import fixed_mill_node_capabilities
    from .node_capability_shapes import fixed_draw_node_capabilities
    from .source_sacrifice_shapes import fixed_source_sacrifice_node_capabilities

    if len(effects) != 1 or COLLECTIVE_SOURCE_DAMAGE_MECHANIC not in mechanic_ids:
        return ()
    effect = thaw_value(FrozenMap(effects[0]))
    schema = thaw_value(FrozenMap(target_schema)) if isinstance(target_schema, Mapping) else None
    if set(effect) != _EFFECT_FIELDS:
        return ()
    instruction = effect[COLLECTIVE_SOURCE_DAMAGE_FIELD]
    if (not isinstance(instruction, dict) or set(instruction) != {"schema_version", "amount", "audience"}
            or type(instruction["schema_version"]) is not int or instruction["schema_version"] != 1
            or type(instruction["amount"]) is not int or instruction["amount"] <= 0
            or instruction["audience"] not in ("players", "opponents")):
        return ()
    result = effect["result"]
    if not isinstance(result, dict):
        return ()
    amount, audience = instruction["amount"], instruction["audience"]
    if effect != collective_source_damage_instruction(amount, audience, result):
        return ()
    if audience == "players" and target_schema is None:
        following = collective_creature_destruction_capabilities(result, mechanic_ids)
    elif audience == "players":
        if schema != _PLAYER_SCHEMA or type(schema.get("count")) is not int:
            return ()
        count = result.get("count")
        if type(count) is not int or count <= 0 or result.get("player") != "$target.0":
            return ()
        if result.get("op") == "draw":
            following = fixed_draw_node_capabilities(effects=(result,), target_schema=schema, mechanic_ids=mechanic_ids)
        else:
            following = fixed_mill_node_capabilities(effects=(result,), target_schema=schema, mechanic_ids=mechanic_ids)
    elif schema is None and result == fixed_source_sacrifice_effect():
        following = fixed_source_sacrifice_node_capabilities(effects=(result,), target_schema=None, mechanic_ids=mechanic_ids)
    else:
        return ()
    if not following:
        return ()
    required = {COLLECTIVE_SOURCE_DAMAGE_CAPABILITY, "damage.batch.fixed_set", "damage.amount.positive",
                "damage.result.player_life", "damage.source.stack_current_or_lki", *following}
    if audience == "opponents":
        required.add("trigger.placement.apnap")
    return tuple(sorted(required))


def collective_source_damage_node_capabilities(*, effects, target_schema, mechanic_ids):
    return _closed_original(effects, target_schema, mechanic_ids)


def collective_source_damage_runtime_dependencies(program):
    if not contains_nested_key((*program.effects, *getattr(program, "handlers", ())), COLLECTIVE_SOURCE_DAMAGE_FIELD):
        return ()
    required = _closed_original(program.effects, program.target_schema, program.coverage)
    if not required:
        raise ValueError("Collective source damage requires its complete compiled instruction")
    audience = program.effects[0][COLLECTIVE_SOURCE_DAMAGE_FIELD]["audience"]
    origin = ("resolve", "stack") if audience == "players" else ("permanent.enter.self", "battlefield")
    if (program.event, program.active_zone) != origin:
        raise ValueError("Collective source damage lost its original spell or entry frame")
    return required


def is_closed_collective_source_damage_program(program):
    try:
        required = collective_source_damage_runtime_dependencies(program)
    except (ValueError, TypeError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def _checked_round(state, expected):
    if not isinstance(state, dict) or set(state) != {"order", "cursor", "accepted"}:
        raise GameRuleError("Collective damage choice has no valid APNAP continuation")
    order, cursor, accepted = state["order"], state["cursor"], state["accepted"]
    if (order != list(expected) or type(cursor) is not int or not 0 <= cursor < len(order)
            or not isinstance(accepted, list)
            or accepted != [seat for seat in order[:cursor] if seat in accepted]):
        raise GameRuleError("The APNAP damage choice order or prior responses changed")
    return order, cursor, accepted


def bind_collective_source_damage(host, item, effect, *, resuming_actor=None):
    if COLLECTIVE_SOURCE_DAMAGE_FIELD not in effect:
        return effect
    program = host.semantics.get(item.semantic_key)
    if (program is None or not item.context.get("currently_resolving")
            or not host.semantic_program_is_current_trusted(program)
            or not is_closed_collective_source_damage_program(program)):
        raise GameRuleError("Collective source damage requires its trusted resolving spell or trigger")
    expected = thaw_value(FrozenMap(host._semantic_value(program.effects[0], item)))
    incoming = thaw_value(FrozenMap(effect))
    audience = expected[COLLECTIVE_SOURCE_DAMAGE_FIELD]["audience"]
    order = tuple(seat for seat in host.apnap_order() if audience == "players" or seat != item.controller)
    if not order:
        raise GameRuleError("Collective source damage has no active eligible player")
    progress = incoming.pop(ROUND_FIELD, None)
    if progress is None:
        if resuming_actor is not None:
            raise GameRuleError("The recorded damage choice lost its APNAP continuation")
        progress = {"order": list(order), "cursor": 0, "accepted": []}
    checked, cursor, _ = _checked_round(progress, order)
    payer = checked[cursor]
    if resuming_actor is not None:
        if (payer != resuming_actor or incoming.get("player") != payer
                or incoming.get("_choice_actor") != payer or incoming.get("_legal_values") != list(_CHOICES)):
            raise GameRuleError("The recorded damage choice changed its authorized payer")
        incoming = {key: value for key, value in incoming.items() if key not in _CONTINUATION_FIELDS}
        incoming["player"] = item.controller
    if incoming != expected:
        raise GameRuleError("Collective source damage changed its original options or result")
    return {**expected, "player": payer, ROUND_FIELD: progress}


def collective_damage_option_followup(effect, *, choice, actor, controller, active_seats):
    payload = thaw_value(effect)
    progress = payload[ROUND_FIELD]
    order, cursor, accepted = _checked_round(progress, tuple(progress.get("order", ())))
    if actor != order[cursor] or any(seat not in active_seats for seat in order):
        raise GameRuleError("The collective damage chooser no longer belongs to the round")
    if choice == "accept_damage":
        accepted = [*accepted, actor]
    if cursor + 1 < len(order):
        following = {key: item for key, item in payload.items() if key not in _CONTINUATION_FIELDS}
        following.update(player=controller, **{ROUND_FIELD: {"order": order, "cursor": cursor + 1, "accepted": accepted}})
        return (following,)
    instruction = payload[COLLECTIVE_SOURCE_DAMAGE_FIELD]
    if not accepted:
        return (payload["result"],) if instruction["audience"] == "players" else ()
    amount = instruction["amount"]
    damage = {"op": "damage_fixed_set", "source": "$source", "amount": amount,
              COLLECTIVE_DAMAGE_RECIPIENTS: {"schema_version": 1, "seats": accepted}}
    return (damage, payload["result"]) if instruction["audience"] == "opponents" else (damage,)


def allocated_collective_damage(value, *, amount, query):
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "seats"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(amount) is not int or amount <= 0):
        raise ValueError("Collective damage requires its selected players and fixed amount")
    seats = value["seats"]
    if (not isinstance(seats, (list, tuple)) or not seats or any(type(seat) is not str for seat in seats)
            or list(seats) != [seat for seat in query.apnap_order if seat in seats]):
        raise ValueError("Collective damage recipients changed their active APNAP order")
    return tuple(AllocatedDamageRecipient(seat, None, amount) for seat in seats)
