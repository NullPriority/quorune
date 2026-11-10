from __future__ import annotations

"""Close ordered counter effects that use the selected stack object's controller."""

from collections.abc import Mapping

from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from ..object_predicate import ObjectQuerySpec
from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_CAPABILITY, PublicQueryAmountSpec
from ..replacement.immutable import thaw_value


STACK_COUNTER_FOLLOWUP_OPERATION = "resolve_stack_counter_followup"
STACK_COUNTER_FOLLOWUP_CAPABILITY = "stack.counter.controller_followup"
STACK_TARGET_CONTROLLER = "$target.current_controller.0"
_COUNTER = {"op": "counter_stack_target", "stack": "$target.0"}
STACK_FOLLOWUP_PLAYER_SCHEMA = {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"}
_CAST_PROHIBITION = "prohibit_casting_until_end_of_turn"
_RECIPIENT_EFFECTS = frozenset({"draw", "lose_life", "mill", "choose_cards_apnap", "create_token", _CAST_PROHIBITION})
_SECOND_EFFECT = {"draw": "draw", "lose_life": "life"}


def _recipient_binding(op, recipient):
    if op == "choose_cards_apnap":
        return {"players": [recipient]}
    return {"controller" if op == "create_token" else "player": recipient}


def with_stack_counter_recipient(effect, recipient):
    return {**effect, **_recipient_binding(effect["op"], recipient)}


def _leaf_dependencies(effect, *, recipient):
    from .affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities
    from .fixed_controller_effect_shapes import fixed_life_node_capabilities
    from .mill_capability_shapes import fixed_mill_node_capabilities
    from .node_capability_shapes import fixed_draw_node_capabilities

    op = effect.get("op")
    if any(effect.get(field) != expected for field, expected in _recipient_binding(op, recipient).items()):
        return ()
    projected = with_stack_counter_recipient(effect, "$controller")
    if op == "choose_cards_apnap":
        return fixed_affected_player_discard_node_capabilities(
            effects=(projected,), target_schema=None,
            mechanic_ids=("fixed-affected-player-discard", "cr-402-hand"), allow_controller=True,
        )
    if op == "create_token":
        from .token_creation_capability_shapes import _fixed_token_effect_closure

        closure = _fixed_token_effect_closure(projected)
        return closure[0] if closure else ()
    if op == _CAST_PROHIBITION:
        from .temporary_cast_restrictions import temporary_cast_node_capabilities

        if effect.get("restriction") != {"schema_version": 1, "player_scope": "target", "spell_kind": "any"}:
            return ()
        return temporary_cast_node_capabilities(
            effects=({**effect, "player": "$target.0"},), target_schema=STACK_FOLLOWUP_PLAYER_SCHEMA,
            mechanic_ids=("temporary-cast-prohibition", "cr-115-targets"),
        )
    if op == "draw":
        return fixed_draw_node_capabilities(effects=(projected,), target_schema=None,
                                            mechanic_ids=("cr-121-drawing-a-card",))
    if op == "mill":
        return fixed_mill_node_capabilities(effects=(projected,), target_schema=None, mechanic_ids=("mill",))
    extra = ()
    if op == "lose_life" and isinstance(projected.get("amount"), Mapping):
        quantity = PublicQueryAmountSpec(CharacteristicQuantitySpec(
            CharacteristicQuantityScope.CONTROLLER_ZONE,
            ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
        ))
        if PublicQueryAmountSpec.from_dict(projected["amount"]) != quantity:
            return ()
        projected["amount"] = 1
        extra = (PUBLIC_QUERY_AMOUNT_CAPABILITY,)
    owned = fixed_life_node_capabilities(effects=(projected,), target_schema=None, mechanic_ids=("cr-119-life",))
    return (*owned, *extra) if owned else ()


def validate_stack_counter_followups(value):
    if (not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 2
            or any(not isinstance(effect, Mapping) for effect in value)):
        raise ValueError("Counter followups require one bound result and at most one controller result")
    effects = tuple(thaw_value(effect) for effect in value)
    op = effects[0].get("op")
    if op not in _RECIPIENT_EFFECTS:
        raise ValueError("Counter followup operation is unsupported")
    owned = _leaf_dependencies(effects[0], recipient=STACK_TARGET_CONTROLLER)
    if not owned:
        raise ValueError("Counter followup is missing a closed recipient instruction")
    if len(effects) == 2:
        if effects[1].get("op") != _SECOND_EFFECT.get(op):
            raise ValueError("Counter followup has an unsupported controller result")
        controller = _leaf_dependencies(effects[1], recipient="$controller")
        if not controller:
            raise ValueError("Counter followup has a malformed controller result")
        owned = (*owned, *controller)
    return effects, owned


def _compiled_followups(effect):
    if (set(effect) != {"op", "schema_version", "stack", "followups"}
            or effect.get("op") != STACK_COUNTER_FOLLOWUP_OPERATION
            or type(effect.get("schema_version")) is not int or effect["schema_version"] != 1
            or effect.get("stack") != "$target.0"):
        raise ValueError("Counter followup requires the one declared stack target")
    return validate_stack_counter_followups(effect["followups"])


def stack_counter_followup_node_capabilities(*, effects, target_schema, mechanic_ids):
    if not 1 <= len(effects) <= 2 or effects[0].get("op") != STACK_COUNTER_FOLLOWUP_OPERATION:
        return ()
    from .node_capability_shapes import targeted_counter_node_capabilities

    try:
        followups, owned = _compiled_followups(effects[0])
        if len(effects) == 2:
            # A separate printed draw line remains ordered after the bound result.
            extra = thaw_value(effects[1])
            if len(followups) != 1 or followups[0]["op"] != "draw" or extra.get("op") != "draw":
                return ()
            controller = _leaf_dependencies(extra, recipient="$controller")
            if not controller:
                return ()
            owned = (*owned, *controller)
        counter = targeted_counter_node_capabilities(
            effects=(_COUNTER,), target_schema=thaw_value(target_schema), mechanic_ids=("counter", "cr-115-targets"),
        )
    except (ValueError, TypeError, KeyError):
        return ()
    return tuple(sorted({STACK_COUNTER_FOLLOWUP_CAPABILITY, *counter, *owned})) if counter else ()


def _contains_followup(value):
    if isinstance(value, Mapping):
        return value.get("op") == STACK_COUNTER_FOLLOWUP_OPERATION or any(map(_contains_followup, value.values()))
    return isinstance(value, (list, tuple)) and any(map(_contains_followup, value))


def stack_counter_followup_runtime_dependencies(program):
    if not _contains_followup(program.effects):
        return ()
    owned = stack_counter_followup_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not owned:
        raise ValueError("Counter followup lacks its closed stack target and ordered result")
    return owned


def is_closed_stack_counter_followup_program(program):
    owned = stack_counter_followup_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(owned) and set(owned) <= set(program.capability_dependencies)


def materialize_stack_counter_followup(effect, *, resolve):
    followups, _ = _compiled_followups(effect)
    stack = resolve(effect["stack"])
    if stack is None:
        return {"op": STACK_COUNTER_FOLLOWUP_OPERATION, "stack": None}
    recipient = resolve(STACK_TARGET_CONTROLLER)
    if type(stack) is not str or not stack or type(recipient) is not str or not recipient:
        raise ValueError("Counter followup requires the current controller of the selected stack object")
    return {**effect, "stack": stack, "recipient": recipient, "followups": followups}


def bound_stack_counter_instructions(effect, *, active_seats):
    if (set(effect) != {"op", "schema_version", "stack", "recipient", "followups"}
            or effect.get("op") != STACK_COUNTER_FOLLOWUP_OPERATION
            or type(effect.get("schema_version")) is not int or effect["schema_version"] != 1
            or type(effect.get("stack")) is not str or not effect["stack"]
            or type(effect.get("recipient")) is not str or effect["recipient"] not in active_seats):
        raise ValueError("Counter followup requires a bound stack object and active player")
    followups, _ = validate_stack_counter_followups(effect["followups"])
    first, *remaining = followups
    return ({**_COUNTER, "stack": effect["stack"]}, with_stack_counter_recipient(first, effect["recipient"]), *remaining)
