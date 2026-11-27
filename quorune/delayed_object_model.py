"""CR 603.7 actions on an exact object incarnation in its required zone."""
from collections.abc import Mapping
from typing import Any
from .counter_names import normalized_counter_name
from .damage_recipient_model import DAMAGE_PERMANENT_OBJECT,DAMAGE_PERMANENT_CAPABILITY

DELAYED_OBJECT_CAPABILITY='trigger.delayed.fixed_object_action'
DELAYED_OBJECT_MECHANIC='fixed-delayed-object-action'
SCHEDULE_OBJECT_ACTION='schedule_fixed_object_action'
RESOLVE_OBJECT_ACTION='resolve_fixed_object_action'
OBJECT_ACTIONS=frozenset({'destroy','sacrifice','bounce'})
COUNTER_OBJECT_ACTIONS=frozenset({'place_counters','remove_counters'})
OBJECT_TIMINGS={'end_of_combat':{'phase':'combat','step':'end_combat'},
    'next_end_step':{'phase':'ending','step':'end_step'}}
OBJECT_REFERENCES=frozenset({'$source.zone_object','$event.block_counterpart',DAMAGE_PERMANENT_OBJECT})


FORCED_BLOCK_STATUS_VERSION = 4
GRAVEYARD_RETURN_VERSION = 5
GRAVEYARD_COUNTER_RETURN_VERSION = 6
CONTROLLED_ATTACKER_PHASE_OUT_VERSION = 7
DELAYED_SOURCE_CONVERT_VERSION = 10
ARTIFACT_GRAVEYARD_RETURN_VERSION = 11
ARTIFACT_GRAVEYARD_RETURN_EVENT = "artifact.graveyard"
ARTIFACT_GRAVEYARD_COMPOSITION_FIELD = "delayed_artifact_graveyard_arrivals"
DEATH_BATTLEFIELD_ARRIVALS = "death_battlefield_arrivals"
SOURCE_DEATH_HAND_VERSION = 12
SOURCE_DEATH_HAND_ACTION = "return_source_death_to_owner_hand"
SOURCE_DEATH_HAND_TEMPLATE = "source-death-return-owner-hand-next-end-step-v1"
SOURCE_DEATH_HAND_CAPABILITY = "trigger.delayed.source_death_owner_hand"
SOURCE_DEATH_HAND_EVENT = "permanent.graveyard.self"
SOURCE_DEATH_HAND_DEPENDENCIES = (
    SOURCE_DEATH_HAND_CAPABILITY, DELAYED_OBJECT_CAPABILITY, "trigger.effect.fixed_event",
    "trigger.event.normalized_zone_change", "trigger.placement.apnap",
    "zone.change.destination_replacement",
)
ARTIFACT_GRAVEYARD_RETURN_DEPENDENCIES = (
    DELAYED_OBJECT_CAPABILITY, "trigger.event.normalized_zone_change", "trigger.placement.apnap",
    "zone.change.destination_replacement", "zone.return.fixed_event_battlefield",
)
DELAYED_SOURCE_CONVERT_CAPABILITY = "transform.delayed.source_end_combat"
PERMANENT_CONVERT_CAPABILITY = "permanent.convert.face_change"
DELAYED_SOURCE_CONVERT_DEPENDENCIES = (
    DELAYED_OBJECT_CAPABILITY,
    DELAYED_SOURCE_CONVERT_CAPABILITY,
    PERMANENT_CONVERT_CAPABILITY,
    "trigger.effect.fixed_event",
    "trigger.event.normalized_damage",
    "trigger.placement.apnap",
)
_ACTIONS_BY_VERSION = {
    1: OBJECT_ACTIONS,
    2: COUNTER_OBJECT_ACTIONS,
    GRAVEYARD_RETURN_VERSION: frozenset({"bounce"}),
    GRAVEYARD_COUNTER_RETURN_VERSION: frozenset({"return_with_counter"}),
    CONTROLLED_ATTACKER_PHASE_OUT_VERSION: frozenset({"phase_out"}),
    DELAYED_SOURCE_CONVERT_VERSION: frozenset({"convert"}),
}
FORCED_BLOCK_STATUS_CAPABILITIES = ("combat.declaration.typed_components",
    "target.revalidate_resolution", "trigger.event.normalized_public_action")


def delayed_source_convert_effect():
    return {
        "op": SCHEDULE_OBJECT_ACTION, "schema_version": DELAYED_SOURCE_CONVERT_VERSION,
        "object": "$source.zone_object", "action": "convert", "timing": "end_of_combat",
    }


def resolved_source_convert_effect(*, reference, logical_object_id, transform_count):
    return {
        "op": RESOLVE_OBJECT_ACTION, "schema_version": DELAYED_SOURCE_CONVERT_VERSION,
        "object": {"reference": reference, "logical_object_id": logical_object_id},
        "action": "convert", "expected_transform_count": transform_count,
    }


def controlled_attacker_delayed_phase_out_effect():
    return {"op": SCHEDULE_OBJECT_ACTION, "schema_version": CONTROLLED_ATTACKER_PHASE_OUT_VERSION,
            "object": "$event.permanent_object", "action": "phase_out", "timing": "end_of_combat"}


def controlled_attacker_delayed_phase_out_capabilities():
    from .phasing_model import PHASE_OUT_CAPABILITY

    return (DELAYED_OBJECT_CAPABILITY, PHASE_OUT_CAPABILITY, "resolution.event_object.fixed_result",
            "trigger.event.normalized_public_action", "trigger.placement.apnap", "trigger.effect.fixed_event")


def forced_block_status_target_schema() -> dict[str, Any]:
    return {
        "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
        "combat_state": "unblocked_attacking", "count": 1,
    }


def forced_block_status_effect() -> dict[str, Any]:
    return {"op": RESOLVE_OBJECT_ACTION, "schema_version": FORCED_BLOCK_STATUS_VERSION,
            "action": "become_blocked", "object": "$target.0"}


def is_forced_block_status_effect(effect: Mapping[str, Any]) -> bool:
    return (isinstance(effect, Mapping) and effect.get("op") == RESOLVE_OBJECT_ACTION
            and (effect.get("schema_version") == FORCED_BLOCK_STATUS_VERSION or effect.get("action") == "become_blocked"))


def validate_forced_block_status_effect(effect: Mapping[str, Any], *, compiled: bool) -> None:
    expected = forced_block_status_effect()
    if (not isinstance(effect, Mapping) or set(effect) != set(expected)
            or type(effect["schema_version"]) is not int
            or {**effect, "object": "$target.0"} != expected):
        raise ValueError("A blocked-status effect requires one exact current attacker")
    value = effect["object"]
    if compiled and value == "$target.0" or not compiled and value is None:
        return
    if (compiled or not isinstance(value, Mapping) or set(value) != {"reference", "logical_object_id"}
            or any(type(text) is not str or not text or text.startswith("$") for text in value.values())):
        raise ValueError("A resolved blocked-status effect requires its exact attacking incarnation")


def source_death_hand_return_effect():
    return {
        "op": SCHEDULE_OBJECT_ACTION, "schema_version": SOURCE_DEATH_HAND_VERSION,
        "object": "$context." + DEATH_BATTLEFIELD_ARRIVALS, "action": SOURCE_DEATH_HAND_ACTION,
        "timing": "next_end_step",
    }


def uses_source_death_hand_return(effect):
    return (effect.get("op") in {SCHEDULE_OBJECT_ACTION, RESOLVE_OBJECT_ACTION}
            and (effect.get("schema_version") == SOURCE_DEATH_HAND_VERSION
                 or effect.get("action") == SOURCE_DEATH_HAND_ACTION))


def validate_source_death_hand_return(effect, *, compiled=False):
    if not isinstance(effect, Mapping) or type(effect.get("schema_version")) is not int:
        raise ValueError("Delayed owner-hand return requires its exact instruction version")
    expected = source_death_hand_return_effect()
    if compiled:
        if dict(effect) != expected:
            raise ValueError("Delayed owner-hand return requires the original self-death instruction")
        return
    values = dict(effect)
    if values.get("op") == RESOLVE_OBJECT_ACTION:
        expected["op"] = RESOLVE_OBJECT_ACTION
        expected.pop("timing")
        selections = values.pop("_replacement_selections", ())
        if not isinstance(selections, (tuple, list)):
            raise ValueError("Delayed owner-hand replacement selections require a sequence")
    if (not isinstance(values.get("object"), (tuple, list))
            or {**values, "object": expected["object"]} != expected):
        raise ValueError("The materialized owner-hand instruction changed")


def validate_delayed_object_instruction(effect,*,compiled=False):
    if not isinstance(effect,Mapping):raise ValueError('Delayed object instruction must be a mapping')
    if uses_source_death_hand_return(effect):
        validate_source_death_hand_return(effect, compiled=compiled)
        return effect
    if is_artifact_graveyard_return(effect):
        return validate_artifact_graveyard_return(effect, compiled=compiled)
    if is_source_bound_creature_return(effect):
        validate_source_bound_creature_return(effect, compiled=compiled)
        return effect
    if is_target_death_return_effect(effect):
        validate_target_death_return_effect(effect, compiled=compiled)
        return effect
    if is_forced_block_status_effect(effect):
        validate_forced_block_status_effect(effect, compiled=compiled)
        return effect
    op=effect.get('op');version=effect.get('schema_version');fields={'op','schema_version','object','action'}
    if type(version)is int and version==2:fields.update({'counter','amount','source'})
    if op==SCHEDULE_OBJECT_ACTION:fields.add('timing')
    if version == DELAYED_SOURCE_CONVERT_VERSION and not compiled:
        fields.add("expected_transform_count")
    if (op not in {SCHEDULE_OBJECT_ACTION,RESOLVE_OBJECT_ACTION} or set(effect)!=fields
            or type(version)is not int or version not in _ACTIONS_BY_VERSION
            or type(effect['action'])is not str or effect['action'] not in _ACTIONS_BY_VERSION[version]):
        raise ValueError('Delayed object action has a closed version and operation')
    if version == GRAVEYARD_COUNTER_RETURN_VERSION and (
            op != SCHEDULE_OBJECT_ACTION or effect["timing"] != "next_end_step"
            or (compiled and effect["object"] != "$source.zone_object")
    ):
        raise ValueError("Counter return requires the discarded source at the next end step")
    if version == GRAVEYARD_RETURN_VERSION and (
            effect["action"] != "bounce"
            or (op == SCHEDULE_OBJECT_ACTION and effect["timing"] != "next_end_step")
            or (compiled and effect["object"] != "$source.zone_object")
    ):
        raise ValueError("Graveyard return requires the original source at the next end step")
    if version == CONTROLLED_ATTACKER_PHASE_OUT_VERSION and (
            op == SCHEDULE_OBJECT_ACTION and effect["timing"] != "end_of_combat"
            or compiled and dict(effect) != controlled_attacker_delayed_phase_out_effect()
    ):
        raise ValueError("Delayed phasing requires the original attacker at end of combat")
    if version == DELAYED_SOURCE_CONVERT_VERSION:
        if (compiled and dict(effect) != delayed_source_convert_effect()
                or op == SCHEDULE_OBJECT_ACTION and effect["timing"] != "end_of_combat"):
            raise ValueError("Delayed conversion requires its original source at end of combat")
        if not compiled:
            count = effect["expected_transform_count"]
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("Delayed conversion requires its nonnegative creation count")
            if effect["object"] is None and count is None:
                return effect
    if version==2 and (type(effect['counter'])is not str or not effect['counter']
            or normalized_counter_name(effect['counter'])!=effect['counter']
            or type(effect['amount'])is not int or effect['amount']<=0
            or type(effect['source'])is not str or not effect['source']
            or compiled and (effect['object']!='$source.zone_object' or effect['source']!='$source')
            or not compiled and effect['source'].startswith('$')):
        raise ValueError('Delayed counters require one canonical fixed amount, name and creating source')
    if op==SCHEDULE_OBJECT_ACTION and (type(effect['timing'])is not str or effect['timing']not in OBJECT_TIMINGS):
        raise ValueError('Delayed object timing is outside the closed family')
    obj=effect['object']
    if compiled:
        references = ({"$event.permanent_object"} if version == CONTROLLED_ATTACKER_PHASE_OUT_VERSION
                      else OBJECT_REFERENCES)
        if op!=SCHEDULE_OBJECT_ACTION or type(obj)is not str or obj not in references:
            raise ValueError('Compiled delayed object must have one sealed source or counterpart reference')
    elif (not isinstance(obj,Mapping) or set(obj)!={'reference','logical_object_id'}
            or any(type(v)is not str or not v for v in obj.values())):
        raise ValueError('Delayed object needs its paired reference and incarnation')
    return effect


SOURCE_BOUND_CREATURE_RETURN_VERSION = 8
SOURCE_BOUND_CREATURE_RETURN_CAPABILITY = "trigger.delayed.source_bound_graveyard_return"
SMALL_CREATURE_DEATH_ARRIVALS = "small_creature_death_arrivals"
SOURCE_BOUND_CREATURE_RETURN_DEPENDENCIES = (
    DELAYED_OBJECT_CAPABILITY, SOURCE_BOUND_CREATURE_RETURN_CAPABILITY, "effect.choice.optional_fixed",
    "trigger.event.qualified_zone_change", "trigger.placement.apnap", "zone.change.destination_replacement",
)
SOURCE_BOUND_RETURN_OFFER = "source_bound_return"
SOURCE_BOUND_RETURN_ACCEPTED = "return_source_bound_death_members"


def small_creature_owner_graveyard_condition() -> dict:
    return {"all": [
        {"all": [
            {"field": "types", "op": "contains_any", "value": ["creature"]},
            {"field": "power", "op": "lte", "value": 1},
        ]},
        {"field": "owner", "op": "eq", "value": "$source.controller"},
    ]}


def source_bound_creature_return_effect() -> dict:
    return {
        "op": SCHEDULE_OBJECT_ACTION, "schema_version": SOURCE_BOUND_CREATURE_RETURN_VERSION,
        "action": SOURCE_BOUND_RETURN_OFFER, "object": "$context." + SMALL_CREATURE_DEATH_ARRIVALS,
        "source": "$source.zone_object", "timing": "next_end_step",
    }


def is_source_bound_creature_return(effect) -> bool:
    return (effect.get("op") in {SCHEDULE_OBJECT_ACTION, RESOLVE_OBJECT_ACTION}
            and effect.get("schema_version") == SOURCE_BOUND_CREATURE_RETURN_VERSION)


def is_accepted_source_bound_creature_return(effect) -> bool:
    return (is_source_bound_creature_return(effect) and effect["op"] == RESOLVE_OBJECT_ACTION
            and effect.get("action") == SOURCE_BOUND_RETURN_ACCEPTED)


def decode_source_bound_return_arrivals(value):
    from .continuous_effect_model import ContinuousObjectIdentity

    if not isinstance(value, (list, tuple)):
        raise ValueError("Source-bound return requires the original graveyard arrivals")
    arrivals = tuple(ContinuousObjectIdentity.from_dict(row) for row in value)
    if len({row.object_id for row in arrivals}) != len(arrivals):
        raise ValueError("Source-bound return cannot repeat a graveyard arrival")
    return arrivals


def validate_source_bound_creature_return(effect, *, compiled=False):
    from .continuous_effect_model import ContinuousObjectIdentity

    expected = source_bound_creature_return_effect()
    if compiled:
        if effect != expected or type(effect["schema_version"]) is not int:
            raise ValueError("Source-bound return requires the exact printed delayed instruction")
        return
    required = set(expected)
    allowed = set(required)
    if effect["op"] == RESOLVE_OBJECT_ACTION:
        required.remove("timing")
        allowed.remove("timing")
        if is_accepted_source_bound_creature_return(effect):
            allowed.update(("_aura_target_refs", "_replacement_selections"))
    if (not required <= set(effect) <= allowed or type(effect["schema_version"]) is not int
            or effect["action"] not in {SOURCE_BOUND_RETURN_OFFER, SOURCE_BOUND_RETURN_ACCEPTED}
            or effect["op"] == SCHEDULE_OBJECT_ACTION
            and (effect["action"] != SOURCE_BOUND_RETURN_OFFER or effect["timing"] != "next_end_step")):
        raise ValueError("Source-bound return has an invalid operation or phase")
    ContinuousObjectIdentity.from_dict(effect["source"])
    arrivals = decode_source_bound_return_arrivals(effect["object"])
    attached = effect.get("_aura_target_refs", {})
    if (not isinstance(attached, Mapping) or not set(attached) <= {row.object_id for row in arrivals}
            or any(type(ref) is not str or not ref for ref in attached.values())
            or not isinstance(effect.get("_replacement_selections", ()), (tuple, list))):
        raise ValueError("Source-bound return choices must refer to the original arrivals")


def delayed_object_node_capabilities(*,effects,target_schema,mechanic_ids):
    if DELAYED_OBJECT_MECHANIC not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    if effects[0] == source_bound_creature_return_effect():
        return SOURCE_BOUND_CREATURE_RETURN_DEPENDENCIES
    try:effect=validate_delayed_object_instruction(effects[0],compiled=True)
    except (ValueError,TypeError,KeyError):return ()
    if uses_source_death_hand_return(effect):
        return SOURCE_DEATH_HAND_DEPENDENCIES
    if is_artifact_graveyard_return(effect):
        return ARTIFACT_GRAVEYARD_RETURN_DEPENDENCIES
    if effect["schema_version"] == CONTROLLED_ATTACKER_PHASE_OUT_VERSION:
        return controlled_attacker_delayed_phase_out_capabilities()
    if effect["schema_version"] == DELAYED_SOURCE_CONVERT_VERSION:
        return DELAYED_SOURCE_CONVERT_DEPENDENCIES
    if effect['schema_version']!=1:return ()
    action_caps={'destroy':'permanent.destroy.effect','sacrifice':'choice.affected_player.fixed_sacrifice',
        'bounce':'permanent.return.owner_hand'}
    return (DELAYED_OBJECT_CAPABILITY,'trigger.placement.apnap','zone.change.destination_replacement',action_caps[effect['action']],
        *((DAMAGE_PERMANENT_CAPABILITY,) if effect['object']==DAMAGE_PERMANENT_OBJECT else ()))


def artifact_graveyard_return_condition():
    return {"field": "owner", "op": "eq", "value": "$source.controller"}


def artifact_graveyard_return_effect():
    return {
        "op": SCHEDULE_OBJECT_ACTION, "schema_version": ARTIFACT_GRAVEYARD_RETURN_VERSION,
        "object": "$context.card", "action": "return_artifact_graveyard_occurrence", "timing": "next_end_step",
    }


def is_artifact_graveyard_return(effect):
    return (effect.get("op") in (SCHEDULE_OBJECT_ACTION, RESOLVE_OBJECT_ACTION)
            and effect.get("schema_version") == ARTIFACT_GRAVEYARD_RETURN_VERSION)


def validate_artifact_graveyard_return(effect, *, compiled=False):
    expected = artifact_graveyard_return_effect()
    if compiled:
        if type(effect.get("schema_version")) is not int or dict(effect) != expected:
            raise ValueError("Artifact return requires an exact next-end-step graveyard occurrence")
        return effect
    original = dict(effect)
    if original.get("op") == RESOLVE_OBJECT_ACTION:
        expected["op"] = RESOLVE_OBJECT_ACTION
        expected.pop("timing")
        replacements = original.pop("_replacement_selections", ())
        auras = original.pop("_aura_target_refs", {})
        if not isinstance(replacements, (list, tuple)) or not isinstance(auras, Mapping):
            raise ValueError("Artifact return entry selections have a closed shape")
    arrivals = original.get("object")
    if (set(original) != set(expected) or type(original.get("schema_version")) is not int
            or {**original, "object": expected["object"]} != expected):
        raise ValueError("Artifact return requires an exact next-end-step graveyard occurrence")
    if not isinstance(arrivals, (list, tuple)) or any(not isinstance(arrival, Mapping) for arrival in arrivals):
        raise ValueError("Artifact return requires its original graveyard arrival")
    return effect


TARGET_DEATH_RETURN_VERSION = 9
TARGET_DEATH_RETURN_CAPABILITY = "trigger.delayed.target_death_return_to_hand"
TARGET_DEATH_RETURN_EVENT = "permanent.graveyard.target_death_return"
TARGET_DEATH_RETURN_CONDITION = "target_death_return_object_identity"
TARGET_DEATH_RETURN_MECHANICS = (DELAYED_OBJECT_MECHANIC, "cr-115-targets")
TARGET_DEATH_RETURN_DEPENDENCIES = (
    TARGET_DEATH_RETURN_CAPABILITY, DELAYED_OBJECT_CAPABILITY, "state_query.permanent.any_counter",
    "target.revalidate_resolution", "trigger.event.normalized_zone_change", "trigger.placement.apnap",
    "zone.change.destination_replacement",
)
_TARGET_DEATH_RETURN_ACTION = "return_to_owner_hand"
_TARGET_DEATH_RETURN_TIMING = "if_target_dies_this_turn"
_TARGET_DEATH_RETURN_PRICE_FLAGS = ("tap_source", "untap_source", "discard_source", "sacrifice_source", "exile_source")
_TARGET_DEATH_RETURN_PRICE_SEQUENCES = ("complex_symbols", "choices", "uncompiled_costs")
_TARGET_DEATH_RETURN_PRICE_ZERO = ("life_payment", "energy_payment")
_TARGET_DEATH_RETURN_PRICE_NULL = ("loyalty_delta", "source_counter_removal_cost")
_TARGET_DEATH_RETURN_PRICE_FIELDS = {
    "text", "mana", *_TARGET_DEATH_RETURN_PRICE_FLAGS, *_TARGET_DEATH_RETURN_PRICE_SEQUENCES,
    *_TARGET_DEATH_RETURN_PRICE_ZERO, *_TARGET_DEATH_RETURN_PRICE_NULL,
}


def target_death_return_target_schema() -> dict[str, Any]:
    from .object_predicate import PermanentStatePredicateSpec

    return {
        "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "count": 1,
        "state_predicate": PermanentStatePredicateSpec(minimum_total_counter_count=1).to_dict(),
    }


def target_death_return_effect() -> dict[str, Any]:
    return {
        "op": SCHEDULE_OBJECT_ACTION, "schema_version": TARGET_DEATH_RETURN_VERSION,
        "object": "$target.0", "action": _TARGET_DEATH_RETURN_ACTION, "timing": _TARGET_DEATH_RETURN_TIMING,
    }


def target_death_return_resolution_effect(arrivals) -> dict[str, Any]:
    return {
        "op": RESOLVE_OBJECT_ACTION, "schema_version": TARGET_DEATH_RETURN_VERSION,
        "object": [arrival.to_dict() for arrival in arrivals], "action": _TARGET_DEATH_RETURN_ACTION,
    }


def is_target_death_return_effect(effect) -> bool:
    return (effect.get("op") in (SCHEDULE_OBJECT_ACTION, RESOLVE_OBJECT_ACTION)
            and (effect.get("schema_version") == TARGET_DEATH_RETURN_VERSION
                 or effect.get("action") == _TARGET_DEATH_RETURN_ACTION))


def target_death_return_arrivals(value):
    from .continuous_effect_model import ContinuousObjectIdentity

    if not isinstance(value, (list, tuple)):
        raise ValueError("Death return requires the original graveyard arrivals")
    arrivals = tuple(ContinuousObjectIdentity.from_dict(row) for row in value)
    if len({arrival.object_id for arrival in arrivals}) != len(arrivals):
        raise ValueError("Death return cannot repeat a graveyard arrival")
    return arrivals


def validate_target_death_return_effect(effect, *, compiled=False) -> None:
    from .continuous_effect_model import ContinuousObjectIdentity

    expected = target_death_return_effect()
    if type(effect.get("schema_version")) is not int or effect["schema_version"] != TARGET_DEATH_RETURN_VERSION:
        raise ValueError("Death return requires its exact instruction version")
    if compiled:
        if effect != expected:
            raise ValueError("Death return must select one exact counter-qualified target")
    elif effect.get("op") == SCHEDULE_OBJECT_ACTION:
        if {**effect, "object": "$target.0"} != expected:
            raise ValueError("Death return must schedule the original target for this turn")
        ContinuousObjectIdentity.from_dict(effect["object"])
    else:
        arrivals = target_death_return_arrivals(effect.get("object"))
        instruction = {key: value for key, value in effect.items() if key != "_replacement_selections"}
        instruction["object"] = [arrival.to_dict() for arrival in arrivals]
        if (instruction != target_death_return_resolution_effect(arrivals)
                or not isinstance(effect.get("_replacement_selections", ()), (list, tuple))):
            raise ValueError("Death return must resolve only the original graveyard arrivals")


def target_death_return_node_capabilities(*, effects, target_schema, mechanic_ids):
    from .replacement.immutable import FrozenMap

    if (tuple(effects) != (target_death_return_effect(),)
            or type(effects[0]["schema_version"]) is not int
            or set(mechanic_ids) != set(TARGET_DEATH_RETURN_MECHANICS)
            or not isinstance(target_schema, Mapping)
            or FrozenMap(target_schema) != FrozenMap(target_death_return_target_schema())):
        return ()
    return TARGET_DEATH_RETURN_DEPENDENCIES


def target_death_return_price_is_closed(price) -> bool:
    from re import fullmatch

    from .util import ORDINARY_FIXED_MANA_PATTERN, mana_cost_to_vector

    if not isinstance(price, Mapping) or set(price) != _TARGET_DEATH_RETURN_PRICE_FIELDS:
        return False
    text = price["text"]
    if type(text) is not str or fullmatch(ORDINARY_FIXED_MANA_PATTERN, text) is None:
        return False
    mana = price["mana"]
    expected, _ = mana_cost_to_vector(text)
    return (
        isinstance(mana, Mapping) and any(expected.values()) and dict(mana) == expected
        and all(type(amount) is int for amount in mana.values())
        and all(price[key] is False for key in _TARGET_DEATH_RETURN_PRICE_FLAGS)
        and all(isinstance(price[key], (list, tuple)) and not price[key] for key in _TARGET_DEATH_RETURN_PRICE_SEQUENCES)
        and all(type(price[key]) is int and price[key] == 0 for key in _TARGET_DEATH_RETURN_PRICE_ZERO)
        and all(price[key] is None for key in _TARGET_DEATH_RETURN_PRICE_NULL)
    )
