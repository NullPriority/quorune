from __future__ import annotations

"""Close ordered counter effects using facts about the original stack subject."""

from collections.abc import Mapping
from enum import Enum

from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from ..object_predicate import ObjectQuerySpec
from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_CAPABILITY, PublicQueryAmountSpec
from ..replacement.immutable import FrozenMap, thaw_value
from ..ability_source_information import (
    ACTIVATED_PUBLIC_PERMANENT_SOURCE_FILTER, ABILITY_SOURCE_INFORMATION_CAPABILITY,
)
from .stack_counter_name_scan import COUNTER_NAME_SCAN_OPERATION, counter_name_scan_node_capabilities
from ..stack_counter import (
    COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD, COUNTERED_ABILITY_SOURCE_LOSS_FIELD, CounteredAbilityActivationLock,
)


STACK_COUNTER_FOLLOWUP_OPERATION = "resolve_stack_counter_followup"
STACK_COUNTER_FOLLOWUP_CAPABILITY = "stack.counter.controller_followup"
STACK_TARGET_CONTROLLER = "$target.current_controller.0"
_COUNTER = {"op": "counter_stack_target", "stack": "$target.0"}
STACK_FOLLOWUP_PLAYER_SCHEMA = {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"}
_CAST_PROHIBITION = "prohibit_casting_until_end_of_turn"
STACK_CHARACTERISTIC_FOLLOWUP_VERSION = 2
STACK_MANA_VALUE_FOLLOWUP_VERSION = 3
STACK_BOUND_MANA_DELAY_VERSION = 4
STACK_SHARED_TYPE_HAND_CAST_VERSION = 5
STACK_LIBRARY_COUNTER_POSITION_VERSION = 6
STACK_NEXT_TURN_DRAW_PAIR_VERSION = 7
STACK_NEXT_TURN_DRAW_PAIR_CAPABILITY = "stack.counter.next_turn_draw_pair"
STACK_LIBRARY_COUNTER_POSITION_CAPABILITY = "stack.counter.library_position_choice"
STACK_SHARED_TYPE_HAND_CAST_CAPABILITY = "stack.counter.shared_type_hand_cast"
STACK_MANA_VALUE_FOLLOWUP_CAPABILITY = "stack.counter.mana_value_followup"
_SPELL_TARGET = {"zones": ["stack"], "categories": ["spell"], "source_exclusion": True, "count": 1}


class StackManaValueResult(str, Enum):
    TREASURE = "treasure"
    DRAW = "draw"
    HAND_CAST = "hand_cast"
    NEXT_MAIN_COLORLESS = "next_main_colorless"
    NEXT_PRECOMBAT_COLORS = "next_precombat_colors"


def _stack_mana_value_target(result):
    if result is StackManaValueResult.DRAW:
        return {**_SPELL_TARGET, "types_any": ["creature"]}
    return dict(_SPELL_TARGET)


def stack_mana_value_followup(result):
    return (
        {"op": STACK_COUNTER_FOLLOWUP_OPERATION, "schema_version": STACK_MANA_VALUE_FOLLOWUP_VERSION,
         "stack": "$target.0", "result": result.value},
        _stack_mana_value_target(result),
    )


def _mana_value_followup(effect, *, bound=False):
    fields = {"op", "schema_version", "stack", "result"}
    if bound:
        fields.add("mana_value")
    if (set(effect) != fields or effect.get("op") != STACK_COUNTER_FOLLOWUP_OPERATION
            or type(effect.get("schema_version")) is not int
            or effect["schema_version"] != STACK_MANA_VALUE_FOLLOWUP_VERSION):
        raise ValueError("Mana-value counter followup requires the exact original stack result")
    result = StackManaValueResult(effect["result"])
    if bound:
        if (type(effect["stack"]) is not str or not effect["stack"]
                or type(effect["mana_value"]) is not int or effect["mana_value"] < 0):
            raise ValueError("Mana-value counter followup requires its bound spell and amount")
    elif effect["stack"] != "$target.0":
        raise ValueError("Mana-value counter followup requires one original spell target")
    return result


def _stack_mana_value_node_capabilities(effects, target_schema, mechanic_ids):
    mechanics = set(mechanic_ids)
    if len(effects) != 1 or not {"counter", "cr-115-targets"} <= mechanics:
        return ()
    try:
        result = _mana_value_followup(effects[0])
    except (TypeError, ValueError):
        return ()
    if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or thaw_value(FrozenMap(target_schema)) != _stack_mana_value_target(result)):
        return ()
    if result is StackManaValueResult.DRAW:
        if "cr-121-drawing-a-card" not in mechanics:
            return ()
        output = ("stack.counter.mana_value_draw", "zone.draw.library_to_hand")
    elif result is StackManaValueResult.HAND_CAST:
        from ..bounded_hand_cast_model import BOUNDED_HAND_CAST_CAPABILITY

        output = ("stack.counter.mana_value_hand_cast", BOUNDED_HAND_CAST_CAPABILITY)
    elif result is StackManaValueResult.TREASURE:
        output = ("token.creation.fixed_definition",)
    elif result is StackManaValueResult.NEXT_MAIN_COLORLESS:
        output = ("mana.production.fixed_resolution", "trigger.placement.apnap")
    else:
        output = ("mana.production.fixed_resolution_choice", "mana.production.variable_resolution_choice",
                  "trigger.placement.apnap")
    return (STACK_COUNTER_FOLLOWUP_CAPABILITY, STACK_MANA_VALUE_FOLLOWUP_CAPABILITY,
            "stack.counter.effect", "target.revalidate_resolution", *output)


def captured_stack_target_mana_value(host, ref):
    from .stack_controller_payment_cost import _target_spell_mana_value

    target = next((item for item in host.state.stack if item.ref == ref), None)
    if target is None or target.kind not in {"spell", "spell_copy"}:
        raise ValueError("The revalidated original stack spell is unavailable")
    amount = _target_spell_mana_value(host, target)
    if amount < 0:
        raise ValueError("The original stack spell has an invalid mana value")
    return amount


class StackCounterSubject(str, Enum):
    LEGENDARY_CREATURE_SPELL = "legendary_creature_spell"
    JACE_PLANESWALKER_SPELL = "jace_planeswalker_spell"

    def target_schema(self):
        if self is StackCounterSubject.LEGENDARY_CREATURE_SPELL:
            return {**_SPELL_TARGET, "types_any": ["creature"]}
        return {**_SPELL_TARGET, "colors_any": ["U"]}

    def matches(self, snapshot):
        from ..characteristic_evaluation import type_parts

        if snapshot.get("category") != "spell" or not snapshot.get("stack_id"):
            raise ValueError("Counter followup requires a current original spell")
        types, subtypes, supertypes = type_parts(snapshot["type_line"])
        if self is StackCounterSubject.LEGENDARY_CREATURE_SPELL:
            return "creature" in types and "legendary" in supertypes
        return "planeswalker" in types and "jace" in subtypes


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


def _characteristic_followup(effect, *, bound=False):
    keys = {"op", "schema_version", "stack", "subject", "followups"}
    if bound:
        keys.add("matched")
    stack = effect.get("stack")
    if (
        set(effect) != keys or effect.get("op") != STACK_COUNTER_FOLLOWUP_OPERATION
        or type(effect.get("schema_version")) is not int or effect["schema_version"] != STACK_CHARACTERISTIC_FOLLOWUP_VERSION
        or (bound and (type(stack) is not str or not stack or stack.startswith("$") or type(effect["matched"]) is not bool))
        or (not bound and stack != "$target.0")
    ):
        raise ValueError("Characteristic followup requires its exact original stack target and result")
    subject = StackCounterSubject(effect["subject"])
    followups = effect["followups"]
    if not isinstance(followups, (tuple, list)) or len(followups) != 1 or not isinstance(followups[0], Mapping):
        raise ValueError("Characteristic followup requires one fixed trailing instruction")
    result = thaw_value(followups[0])
    if subject is StackCounterSubject.LEGENDARY_CREATURE_SPELL:
        from ..ring_model import RING_MECHANIC, RING_TEMPTATION_OPERATION
        from .ring_capability_shapes import ring_temptation_node_capabilities

        owned = ring_temptation_node_capabilities(effects=(result,), target_schema=None, mechanic_ids=(RING_MECHANIC,))
        if not owned or result["op"] != RING_TEMPTATION_OPERATION:
            raise ValueError("A legendary creature spell can only qualify the fixed controller Ring instruction")
    else:
        from .node_capability_shapes import fixed_scry_node_capabilities

        owned = fixed_scry_node_capabilities(effects=(result,), target_schema=None, mechanic_ids=("scry",))
        if not owned or result["count"] != 2:
            raise ValueError("A Jace planeswalker spell can only qualify the fixed controller scry two")
    return subject, result, owned


COUNTERED_ABILITY_ACTIVATION_LOCK_CAPABILITY = "stack.counter.activation_prohibition"


def countered_ability_activation_lock_schema(mode):
    schema = {"zones": ["stack"], "categories": ["ability"], "source_exclusion": True, "count": 1}
    if mode is CounteredAbilityActivationLock.AFTER_ATTEMPT:
        schema["predicate"] = ACTIVATED_PUBLIC_PERMANENT_SOURCE_FILTER
    return schema


def countered_ability_activation_lock_effect(mode):
    return {**_COUNTER, COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD: mode.value}


def _countered_ability_activation_lock_capabilities(effects, target_schema, mechanic_ids):
    try:
        mode = CounteredAbilityActivationLock(effects[0].get(COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD))
    except ValueError:
        return ()
    if (
        dict(effects[0]) != countered_ability_activation_lock_effect(mode)
        or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
        or thaw_value(FrozenMap(target_schema)) != countered_ability_activation_lock_schema(mode)
        or not {"counter", "cr-115-targets"} <= set(mechanic_ids)
    ):
        return ()
    dependencies = [COUNTERED_ABILITY_ACTIVATION_LOCK_CAPABILITY, "stack.counter.effect", "target.revalidate_resolution"]
    if mode is CounteredAbilityActivationLock.AFTER_ATTEMPT:
        dependencies.append(ABILITY_SOURCE_INFORMATION_CAPABILITY)
    if len(effects) != 1:
        if mode is not CounteredAbilityActivationLock.AFTER_ATTEMPT or len(effects) != 2:
            return ()
        draw = effects[1]
        if draw.get("op") != "draw" or type(draw.get("count")) is not int or draw["count"] != 1:
            return ()
        owned = _leaf_dependencies(draw, recipient="$controller")
        if not owned:
            return ()
        dependencies.extend(owned)
    return tuple(dependencies)


def countered_ability_source_loss_schema():
    return {"zones": ["stack"], "categories": ["ability"], "source_exclusion": True, "up_to": 1}


def countered_ability_source_loss_effect():
    return {**_COUNTER, COUNTERED_ABILITY_SOURCE_LOSS_FIELD: 1}


def _is_countered_ability_source_loss(effect):
    return effect.get("op") == "counter_stack_target" and COUNTERED_ABILITY_SOURCE_LOSS_FIELD in effect


def _countered_ability_source_loss_capabilities(effects, target_schema, mechanic_ids):
    if (
        len(effects) != 1 or dict(effects[0]) != countered_ability_source_loss_effect()
        or type(effects[0].get(COUNTERED_ABILITY_SOURCE_LOSS_FIELD)) is not int
        or not isinstance(target_schema, Mapping) or type(target_schema.get("up_to")) is not int
        or thaw_value(FrozenMap(target_schema)) != countered_ability_source_loss_schema()
        or not {"counter", "cr-115-targets"} <= set(mechanic_ids)
    ):
        return ()
    return STACK_COUNTER_FOLLOWUP_CAPABILITY, "stack.counter.effect", "target.revalidate_resolution"


def shared_type_hand_cast_followup():
    return ({"op": STACK_COUNTER_FOLLOWUP_OPERATION, "schema_version": STACK_SHARED_TYPE_HAND_CAST_VERSION,
             "stack": "$target.0"}, dict(_SPELL_TARGET))


def _shared_type_hand_cast_node_capabilities(effects, target_schema, mechanic_ids):
    from ..bounded_hand_cast_model import BOUNDED_HAND_CAST_CAPABILITY

    instruction, exact_target = shared_type_hand_cast_followup()
    if (len(effects) != 1 or type(effects[0].get("schema_version")) is not int or thaw_value(effects[0]) != instruction
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or thaw_value(target_schema) != exact_target
            or not {"counter", "cr-115-targets"} <= set(mechanic_ids)):
        return ()
    return (STACK_COUNTER_FOLLOWUP_CAPABILITY, STACK_SHARED_TYPE_HAND_CAST_CAPABILITY, "stack.counter.effect",
            "target.revalidate_resolution", BOUNDED_HAND_CAST_CAPABILITY)


def library_counter_position_followup():
    return ({"op": STACK_COUNTER_FOLLOWUP_OPERATION, "schema_version": STACK_LIBRARY_COUNTER_POSITION_VERSION,
             "stack": "$target.0"}, dict(_SPELL_TARGET))


def _library_counter_position_node_capabilities(effects, target_schema, mechanic_ids):
    from ..zone_trigger_events import COUNTER_DESTINATION_CAPABILITY

    instruction, exact_target = library_counter_position_followup()
    if (len(effects) != 1 or type(effects[0].get("schema_version")) is not int
            or thaw_value(effects[0]) != instruction or not isinstance(target_schema, Mapping)
            or type(target_schema.get("count")) is not int or thaw_value(target_schema) != exact_target
            or not {"counter", "cr-115-targets"} <= set(mechanic_ids)):
        return ()
    return (STACK_COUNTER_FOLLOWUP_CAPABILITY, STACK_LIBRARY_COUNTER_POSITION_CAPABILITY,
            "stack.counter.effect", "target.revalidate_resolution", COUNTER_DESTINATION_CAPABILITY)


def next_turn_draw_pair_followup():
    return ({"op": STACK_COUNTER_FOLLOWUP_OPERATION, "schema_version": STACK_NEXT_TURN_DRAW_PAIR_VERSION,
             "stack": "$target.0"}, dict(_SPELL_TARGET))


def _next_turn_draw_pair_node_capabilities(effects, target_schema, mechanic_ids):
    instruction, exact_target = next_turn_draw_pair_followup()
    required_mechanics = {"counter", "cr-115-targets", "cr-121-drawing-a-card", "cr-603-handling-triggered-abilities"}
    if (len(effects) != 1 or type(effects[0].get("schema_version")) is not int
            or thaw_value(effects[0]) != instruction or not isinstance(target_schema, Mapping)
            or type(target_schema.get("count")) is not int or thaw_value(target_schema) != exact_target
            or not required_mechanics <= set(mechanic_ids)):
        return ()
    return (STACK_COUNTER_FOLLOWUP_CAPABILITY, STACK_NEXT_TURN_DRAW_PAIR_CAPABILITY,
            "stack.counter.effect", "target.revalidate_resolution", "trigger.placement.apnap",
            "zone.draw.library_to_hand")


def stack_counter_followup_node_capabilities(*, effects, target_schema, mechanic_ids):
    if effects and effects[0].get("op") == COUNTER_NAME_SCAN_OPERATION:
        return counter_name_scan_node_capabilities(effects, target_schema, mechanic_ids)
    if effects and effects[0].get("schema_version") == STACK_NEXT_TURN_DRAW_PAIR_VERSION:
        return _next_turn_draw_pair_node_capabilities(effects, target_schema, mechanic_ids)
    if effects and effects[0].get("schema_version") == STACK_LIBRARY_COUNTER_POSITION_VERSION:
        return _library_counter_position_node_capabilities(effects, target_schema, mechanic_ids)
    if effects and effects[0].get("schema_version") == STACK_SHARED_TYPE_HAND_CAST_VERSION:
        return _shared_type_hand_cast_node_capabilities(effects, target_schema, mechanic_ids)
    if effects and effects[0].get("schema_version") == STACK_MANA_VALUE_FOLLOWUP_VERSION:
        return _stack_mana_value_node_capabilities(effects, target_schema, mechanic_ids)
    if effects and COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD in effects[0]:
        return _countered_ability_activation_lock_capabilities(effects, target_schema, mechanic_ids)
    if any(_is_countered_ability_source_loss(effect) for effect in effects):
        return _countered_ability_source_loss_capabilities(effects, target_schema, mechanic_ids)
    if not 1 <= len(effects) <= 2 or effects[0].get("op") != STACK_COUNTER_FOLLOWUP_OPERATION:
        return ()
    from .node_capability_shapes import targeted_counter_node_capabilities

    try:
        if effects[0].get("schema_version") == STACK_CHARACTERISTIC_FOLLOWUP_VERSION:
            subject, _result, owned = _characteristic_followup(effects[0])
            if len(effects) != 1 or thaw_value(target_schema) != subject.target_schema():
                return ()
            followups = ()
        else:
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
        return (value.get("op") in {STACK_COUNTER_FOLLOWUP_OPERATION, COUNTER_NAME_SCAN_OPERATION}
                or _is_countered_ability_source_loss(value)
                or COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD in value
                or any(map(_contains_followup, value.values())))
    return isinstance(value, (list, tuple)) and any(map(_contains_followup, value))


def stack_counter_followup_runtime_dependencies(program):
    if not _contains_followup(program.effects):
        return ()
    owned = stack_counter_followup_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not owned:
        raise ValueError("Counter followup lacks its closed stack target and ordered result")
    if program.effects[0].get("op") == COUNTER_NAME_SCAN_OPERATION and (
        (program.event, program.active_zone) != ("resolve", "stack") or not program.ability_id.startswith("spell:")
        or program.handlers or program.cost_schema is not None or program.event_condition is not None
    ):
        raise ValueError("The different-name exile needs the original resolving counter spell")
    if _is_countered_ability_source_loss(program.effects[0]) and (
        (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
        or program.handlers or program.cost_schema is not None or program.event_condition is not None
    ):
        raise ValueError("Countered ability source loss requires the original source-entry instruction")
    if COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD in program.effects[0] and (
        (program.event, program.active_zone) != ("resolve", "stack")
        or not program.ability_id.startswith("spell:") or program.handlers
        or program.cost_schema is not None or program.event_condition is not None
    ):
        raise ValueError("The ability counter activation prohibition requires its original resolving spell")
    if (program.effects[0].get("schema_version") in {STACK_MANA_VALUE_FOLLOWUP_VERSION,
            STACK_SHARED_TYPE_HAND_CAST_VERSION, STACK_LIBRARY_COUNTER_POSITION_VERSION, STACK_NEXT_TURN_DRAW_PAIR_VERSION}
            and ((program.event, program.active_zone) != ("resolve", "stack")
                 or not program.ability_id.startswith("spell:") or program.handlers
                 or program.cost_schema is not None or program.event_condition is not None)):
        raise ValueError("Captured counter followup requires the original resolving spell")
    if (program.effects[0].get("schema_version") == STACK_CHARACTERISTIC_FOLLOWUP_VERSION
            and ((program.event, program.active_zone) != ("resolve", "stack")
                 or not program.ability_id.startswith("spell:") or program.cost_schema is not None or program.handlers)):
        raise ValueError("Characteristic followup requires its original complete resolving spell")
    return owned


def is_closed_stack_counter_followup_program(program):
    owned = stack_counter_followup_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(owned) and set(owned) <= set(program.capability_dependencies)


def _bind_stack_mana_delay(effect, resolve, source):
    if (
        set(effect) != {"op", "schema_version", "result", "mana_value"}
        or effect.get("op") != STACK_COUNTER_FOLLOWUP_OPERATION
        or type(effect.get("schema_version")) is not int
        or effect["schema_version"] != STACK_BOUND_MANA_DELAY_VERSION
        or type(effect.get("mana_value")) is not int or effect["mana_value"] < 0
    ):
        raise ValueError("Delayed stack mana requires its captured original amount")
    result = StackManaValueResult(effect["result"])
    amount = effect["mana_value"]
    condition = {"player": "controller", "step": "main"}
    if result is StackManaValueResult.NEXT_MAIN_COLORLESS:
        condition["phase"] = ["precombat_main", "postcombat_main"]
        effects = ([{"op": "mana", "player": "$controller", "color": "C", "amount": amount,
                     "source": "$source"}] if amount else [])
    elif result is StackManaValueResult.NEXT_PRECOMBAT_COLORS:
        from ..variable_resolution_mana_model import MANA_COLORS, VariableResolutionManaChoiceSpec

        condition["phase"] = "precombat_main"
        effects = [VariableResolutionManaChoiceSpec("combination", tuple(sorted(MANA_COLORS)), amount).effect()]
    else:
        raise ValueError("Only mana results may wait for a main phase")
    controller = resolve("$controller")
    if type(controller) is not str or not controller or (source is not None and type(source) is not str):
        raise ValueError("Delayed stack mana requires the original resolving role")
    label = "Add mana at the beginning of the main phase"
    return {
        "op": "delayed_trigger", "controller": controller, "source": source,
        "label": label, "event": "step.begin", "condition": condition, "once": True,
        "stack": {"label": label, "context": {"dynamic_effects": effects}},
    }


def _bind_shared_type_hand_cast(effect, resolve, snapshot):
    from ..characteristic_evaluation import type_parts

    raw, _ = shared_type_hand_cast_followup()
    if thaw_value(effect) != raw or type(effect.get("schema_version")) is not int or snapshot is None:
        raise ValueError("Shared-type hand cast needs one declared spell target")
    stack = resolve(effect["stack"])
    if type(stack) is not str or not stack:
        raise ValueError("Shared-type hand cast lost the original spell")
    subject = snapshot(stack)
    if subject.get("category") != "spell" or not subject.get("stack_id"):
        raise ValueError("Shared-type hand cast requires a live spell")
    card_types, _, _ = type_parts(subject["type_line"])
    return {**effect, "stack": stack, "card_types": sorted(card_types)}


def counter_library_position_is_applicable(host, ref):
    from ..stack_counter import stack_item_can_be_countered

    target = next((item for item in host.state.stack if item.ref == ref), None)
    if target is None or target.kind not in {"spell", "spell_copy"}:
        raise ValueError("A counter destination requires the revalidated spell")
    card = host.state.cards.get(target.card_object_id or "")
    return (target.kind == "spell" and card is not None and card.object_kind == "card"
            and card.zone == "stack" and stack_item_can_be_countered(host, target))


def _bind_library_counter_position(effect, resolve, library_position):
    instruction, _ = library_counter_position_followup()
    if type(effect.get("schema_version")) is not int or thaw_value(effect) != instruction or library_position is None:
        raise ValueError("A library counter position requires its original declared spell")
    stack = resolve(effect["stack"])
    if type(stack) is not str or not stack:
        raise ValueError("The revalidated spell for the counter position is unavailable")
    return {**effect, "stack": stack, "needs_position": library_position(stack)}


def bound_library_counter_position(effect):
    if (set(effect) != {"op", "schema_version", "stack", "needs_position"}
            or effect["op"] != STACK_COUNTER_FOLLOWUP_OPERATION
            or type(effect["schema_version"]) is not int
            or effect["schema_version"] != STACK_LIBRARY_COUNTER_POSITION_VERSION
            or type(effect["stack"]) is not str or not effect["stack"] or effect["stack"].startswith("$")
            or type(effect["needs_position"]) is not bool):
        raise ValueError("A library counter position requires its original bound spell")
    return effect["stack"], effect["needs_position"]


def library_position_counter_effect(stack, *, position=None):
    if position is None:
        return {**_COUNTER, "stack": stack}
    if position not in {"top", "bottom"}:
        raise ValueError("Choose the top or bottom of the spell owner's library")
    return {**_COUNTER, "stack": stack, "counter_destination": "library_" + position}


def _bind_next_turn_draw_pair(effect, resolve):
    instruction, _ = next_turn_draw_pair_followup()
    if thaw_value(effect) != instruction or type(effect.get("schema_version")) is not int:
        raise ValueError("Two delayed draws require their original declared spell")
    stack = resolve(effect["stack"])
    recipient = resolve(STACK_TARGET_CONTROLLER)
    if type(stack) is not str or not stack or type(recipient) is not str or not recipient:
        raise ValueError("Two delayed draws require the revalidated spell and its original controller")
    return {**effect, "stack": stack, "recipient": recipient}


def materialize_stack_counter_followup(effect, *, resolve, snapshot=None, mana_value=None,
                                        spell_source_ref=None, library_position=None):
    if effect.get("schema_version") == STACK_NEXT_TURN_DRAW_PAIR_VERSION:
        return _bind_next_turn_draw_pair(effect, resolve)
    if effect.get("schema_version") == STACK_LIBRARY_COUNTER_POSITION_VERSION:
        return _bind_library_counter_position(effect, resolve, library_position)
    if effect.get("schema_version") == STACK_SHARED_TYPE_HAND_CAST_VERSION:
        return _bind_shared_type_hand_cast(effect, resolve, snapshot)
    if effect.get("schema_version") == STACK_BOUND_MANA_DELAY_VERSION:
        return _bind_stack_mana_delay(effect, resolve, spell_source_ref)
    if effect.get("schema_version") == STACK_MANA_VALUE_FOLLOWUP_VERSION:
        _mana_value_followup(effect)
        stack = resolve(effect["stack"])
        if type(stack) is not str or not stack or mana_value is None:
            raise ValueError("Mana-value followup requires a revalidated original spell")
        return {**effect, "stack": stack, "mana_value": mana_value(stack)}
    if effect.get("schema_version") == STACK_CHARACTERISTIC_FOLLOWUP_VERSION:
        subject, followup, _owned = _characteristic_followup(effect)
        stack = resolve(effect["stack"])
        if type(stack) is not str or not stack or snapshot is None:
            raise ValueError("Characteristic followup requires its revalidated stack spell")
        return {**effect, "stack": stack, "matched": subject.matches(snapshot(stack)), "followups": (followup,)}
    followups, _ = _compiled_followups(effect)
    stack = resolve(effect["stack"])
    if stack is None:
        return {"op": STACK_COUNTER_FOLLOWUP_OPERATION, "stack": None}
    recipient = resolve(STACK_TARGET_CONTROLLER)
    if type(stack) is not str or not stack or type(recipient) is not str or not recipient:
        raise ValueError("Counter followup requires the current controller of the selected stack object")
    return {**effect, "stack": stack, "recipient": recipient, "followups": followups}


def _stack_mana_value_result(effect):
    result = _mana_value_followup(effect, bound=True)
    counter = {**_COUNTER, "stack": effect["stack"]}
    amount = effect["mana_value"]
    if result is StackManaValueResult.DRAW:
        draw = {"op": "draw", "player": "$controller", "count": amount, "private": True}
        return (counter, draw) if amount else (counter,)
    if result is StackManaValueResult.HAND_CAST:
        from ..bounded_hand_cast_model import BoundedHandCastSpec
        from ..mana_value_limit import ManaValueLimitSpec

        return counter, BoundedHandCastSpec(ManaValueLimitSpec(amount)).effect()
    if result is StackManaValueResult.TREASURE:
        if amount == 0:
            return (counter,)
        from ..fixed_token_production import predefined_token_identity

        name, characteristics = predefined_token_identity("Treasure", {
            "type_line": "Token Artifact — Treasure",
            "oracle_text": "{T}, Sacrifice this token: Add one mana of any color.",
            "activated_ability_profile": "tap_sac_any_color_mana_v1",
        })
        return counter, {"op": "create_token", "controller": "$controller", "name": name,
                         "quantity": amount, "characteristics": characteristics}
    return counter, {"op": STACK_COUNTER_FOLLOWUP_OPERATION, "schema_version": STACK_BOUND_MANA_DELAY_VERSION,
                     "result": result.value, "mana_value": amount}


def _bound_shared_type_hand_cast_instructions(effect):
    from ..bounded_hand_cast_model import BoundedHandCastSpec

    if (set(effect) != {"op", "schema_version", "stack", "card_types"}
            or effect["op"] != STACK_COUNTER_FOLLOWUP_OPERATION
            or type(effect["schema_version"]) is not int
            or type(effect["stack"]) is not str or not effect["stack"] or effect["stack"].startswith("$")
            or not isinstance(effect["card_types"], (list, tuple))):
        raise ValueError("Shared-type hand cast needs its original bound spell and types")
    spec = BoundedHandCastSpec(None, 2, tuple(effect["card_types"]))
    return {**_COUNTER, "stack": effect["stack"]}, spec.effect()


def _bound_next_turn_draw_pair(effect, active_seats):
    from ..delayed_triggers import counter_next_turn_draw_effects

    if (set(effect) != {"op", "schema_version", "stack", "recipient"}
            or effect["op"] != STACK_COUNTER_FOLLOWUP_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != STACK_NEXT_TURN_DRAW_PAIR_VERSION
            or type(effect["stack"]) is not str or not effect["stack"] or effect["stack"].startswith("$")
            or type(effect["recipient"]) is not str or effect["recipient"] not in active_seats):
        raise ValueError("Two delayed draws require the bound original spell and controller")
    return ({**_COUNTER, "stack": effect["stack"]}, *counter_next_turn_draw_effects(effect["recipient"]))


def bound_stack_counter_instructions(effect, *, active_seats):
    if effect.get("schema_version") == STACK_NEXT_TURN_DRAW_PAIR_VERSION:
        return _bound_next_turn_draw_pair(effect, active_seats)
    if effect.get("schema_version") == STACK_SHARED_TYPE_HAND_CAST_VERSION:
        return _bound_shared_type_hand_cast_instructions(effect)
    if effect.get("schema_version") == STACK_MANA_VALUE_FOLLOWUP_VERSION:
        return _stack_mana_value_result(effect)
    if effect.get("schema_version") == STACK_CHARACTERISTIC_FOLLOWUP_VERSION:
        _subject, result, _owned = _characteristic_followup(effect, bound=True)
        counter = {**_COUNTER, "stack": effect["stack"]}
        return (counter, result) if effect["matched"] else (counter,)
    if (set(effect) != {"op", "schema_version", "stack", "recipient", "followups"}
            or effect.get("op") != STACK_COUNTER_FOLLOWUP_OPERATION
            or type(effect.get("schema_version")) is not int or effect["schema_version"] != 1
            or type(effect.get("stack")) is not str or not effect["stack"]
            or type(effect.get("recipient")) is not str or effect["recipient"] not in active_seats):
        raise ValueError("Counter followup requires a bound stack object and active player")
    followups, _ = validate_stack_counter_followups(effect["followups"])
    first, *remaining = followups
    return ({**_COUNTER, "stack": effect["stack"]}, with_stack_counter_recipient(first, effect["recipient"]), *remaining)
