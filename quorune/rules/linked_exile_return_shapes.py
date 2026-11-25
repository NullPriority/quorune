from __future__ import annotations

"""Exact capability shape for the paired linked exile/return instruction."""

from typing import Any, Iterable, Mapping, Sequence

from ..linked_ability_exile_model import LINKED_ABILITY_EXILE_CAPABILITY
from ..linked_exile_return_model import (
    CONDITIONAL_EXILE_CAPABILITY, LINKED_ABILITY_BINDING_PREFIX, LINKED_ABILITY_TIMING, LINKED_EXILE_RETURN_CAPABILITY,
    LINKED_EXILE_RETURN_MECHANIC, LINKED_EXILE_RETURN_OPERATION, LinkedExileReturnSpec, validate_linked_instruction,
    ANNOUNCED_OPPONENT_EXILE_CAPABILITY, LINKED_TRANSFORMED_RETURN_CAPABILITY,
    LINKED_BATCH_DEPARTURE_RETURN_CAPABILITY, LINKED_FADING_EXILE_CAPABILITY, LINKED_FADING_EXILE_VERSION,
    LINKED_FADING_HAND_BINDING, LinkedFadingScope, validate_linked_ability_binding,
    SAME_NAME_CONDITIONAL_EXILE_CAPABILITY, SAME_NAME_CONDITIONAL_EXILE_VERSION,
)
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..replacement.immutable import thaw_value
from ..targets import TargetGroup
from .activation_counter_cost import SourceCounterRemovalCost


_LINKED_PAIR = (LINKED_EXILE_RETURN_CAPABILITY, LINKED_ABILITY_EXILE_CAPABILITY)
_FADING_PAIR = (*_LINKED_PAIR, LINKED_FADING_EXILE_CAPABILITY)
_OPPONENT_TARGET = {"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1}
_FADE_COST = {
    "mana": dict.fromkeys(("B", "C", "G", "GENERIC", "R", "U", "W"), 0),
    "complex_symbols": [],
    "tap_source": False,
    "untap_source": False,
    "discard_source": False,
    "sacrifice_source": False,
    "exile_source": False,
    "life_payment": 0,
    "energy_payment": 0,
    "loyalty_delta": None,
    "source_counter_removal_cost": SourceCounterRemovalCost("fade", 1).to_dict(),
    "choices": [],
    "uncompiled_costs": [],
}


def uses_linked_fading_exile(effect):
    if LINKED_FADING_HAND_BINDING in effect:
        return True
    spec = effect.get("spec")
    return (effect.get("op") == LINKED_EXILE_RETURN_OPERATION and isinstance(spec, Mapping)
            and ("fading_scope" in spec or spec.get("schema_version") == LINKED_FADING_EXILE_VERSION))


def linked_fading_hand_payload_dependencies(effect, *, target_schema=_OPPONENT_TARGET):
    from .affected_player_discard_capability_shapes import fixed_affected_player_hand_exile_node_capabilities
    from ..compiler.affected_player_discard_templates import FIXED_AFFECTED_PLAYER_HAND_EXILE_MECHANIC

    if thaw_value(target_schema) != _OPPONENT_TARGET:
        return ()
    try:
        validate_linked_ability_binding(effect.get(LINKED_FADING_HAND_BINDING))
    except ValueError:
        return ()
    choice = {key: thaw_value(value) for key, value in effect.items() if key != LINKED_FADING_HAND_BINDING}
    if type(choice.get("count")) is not int or choice["count"] != 1 or choice.get("target") != "$target.0":
        return ()
    required = fixed_affected_player_hand_exile_node_capabilities(
        effects=(choice,), target_schema=_OPPONENT_TARGET,
        mechanic_ids=(FIXED_AFFECTED_PLAYER_HAND_EXILE_MECHANIC, "cr-402-hand", "cr-115-targets"),
    )
    if not required or set(required) != {
            "choice.affected_player.fixed_hand_exile", "target.revalidate_resolution",
            "zone.change.destination_replacement"}:
        return ()
    return (*required, *_FADING_PAIR)


def linked_ability_exile_node_capabilities(*, effects, target_schema, mechanic_ids):
    if LINKED_EXILE_RETURN_MECHANIC not in mechanic_ids or len(effects) != 1:
        return ()
    effect = effects[0]
    if not isinstance(effect, Mapping):
        return ()
    if LINKED_FADING_HAND_BINDING in effect:
        return (linked_fading_hand_payload_dependencies(effect, target_schema=target_schema)
                if "cr-115-targets" in mechanic_ids else ())
    if effect.get("op") != LINKED_EXILE_RETURN_OPERATION:
        return ()
    spec = effect.get("spec")
    if not isinstance(spec, Mapping) or spec.get("timing") != LINKED_ABILITY_TIMING:
        return ()
    if set(effect) != ({"op", "phase", "binding_id", "spec"} | ({"cards"} if effect.get("phase") == "exile" else set())):
        return ()
    try:
        validate_linked_instruction(effect)
    except (TypeError, ValueError):
        return ()
    typed = LinkedExileReturnSpec.from_dict(spec)
    if typed.fading_scope is not None:
        if effect["phase"] == "return":
            return _FADING_PAIR if target_schema is None else ()
        from ..compiler.direct_target import DirectPermanentTargetSpec

        if typed.fading_scope is LinkedFadingScope.OPPONENT_HAND or effect["cards"] != "$targets":
            return ()
        expected = DirectPermanentTargetSpec(types_any=(typed.fading_scope.value,)).to_target_schema()
        if thaw_value(target_schema) != expected or "cr-115-targets" not in mechanic_ids:
            return ()
        return (*_FADING_PAIR, "permanent.exile.effect", "target.revalidate_resolution")
    scope = typed.batch_departure_scope
    if scope is not None:
        from .linked_batch_departure_return import linked_batch_departure_target_schema

        batch = (*_LINKED_PAIR, LINKED_BATCH_DEPARTURE_RETURN_CAPABILITY)
        if effect["phase"] == "return":
            return batch if target_schema is None else ()
        expected = linked_batch_departure_target_schema(scope)
        if effect["cards"] != "$targets" or thaw_value(target_schema) != expected:
            return ()
        batch = (*batch, "permanent.exile.effect")
        if expected is not None:
            return (*batch, "target.revalidate_resolution") if "cr-115-targets" in mechanic_ids else ()
        return batch
    if effect["phase"] == "return":
        return _LINKED_PAIR if target_schema is None else ()
    if effect.get("cards") != "$targets":
        return ()
    from .node_capability_shapes import targeted_exile_node_capabilities
    required = targeted_exile_node_capabilities(effects=({"op": "exile_permanent", "card": "$target.0"},),
        target_schema=target_schema, mechanic_ids=mechanic_ids)
    return (*required, *_LINKED_PAIR) if required else ()


def _uses_ability_link(value):
    if isinstance(value, Mapping):
        if uses_linked_fading_exile(value):
            return True
        spec = value.get("spec")
        binding = value.get("binding_id")
        if value.get("op") == LINKED_EXILE_RETURN_OPERATION and (
            isinstance(binding, str) and binding.startswith(LINKED_ABILITY_BINDING_PREFIX)
            or isinstance(spec, Mapping) and (spec.get("schema_version") == 3 or spec.get("timing") == LINKED_ABILITY_TIMING)
        ):
            return True
        return any(_uses_ability_link(child) for child in value.values())
    return isinstance(value, (list, tuple)) and any(_uses_ability_link(child) for child in value)


def same_name_conditional_exile_target_schema():
    from ..compiler.direct_target import DirectPermanentTargetSpec

    return DirectPermanentTargetSpec(types_none=("land",), controller_relation="opponent").to_target_schema()


def announced_opponent_exile_target_schema():
    from ..target_cardinality import AnnouncedTargetCardinality

    schema = same_name_conditional_exile_target_schema()
    schema.pop("count")
    schema.update(distinct_controllers=True, retain_controller=True,
                  count_parameter=AnnouncedTargetCardinality(
                      selection="up_to", parameter="any_number", schema_version=2).to_dict())
    return schema


def _same_name_conditional_exile_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    specs = (effect.get("spec") for effect in nested_effects((program.effects, program.handlers))
             if effect.get("op") == LINKED_EXILE_RETURN_OPERATION)
    if not any(isinstance(spec, Mapping) and ("same_name_scope" in spec
               or spec.get("schema_version") == SAME_NAME_CONDITIONAL_EXILE_VERSION) for spec in specs):
        return ()
    required = linked_exile_return_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=(LINKED_EXILE_RETURN_MECHANIC, "cr-115-targets"),
    )
    if (SAME_NAME_CONDITIONAL_EXILE_CAPABILITY not in required or program.event != "permanent.enter.self"
            or program.active_zone != "battlefield" or program.event_condition is not None
            or program.cost_schema is not None or program.handlers):
        raise ValueError("Same-name conditional exile requires the closed self-entry and source-departure pair")
    return required


def linked_fading_activation_capabilities(*, effects, target_schema, mechanic_ids, active_zone, cost_schema):
    required = linked_ability_exile_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
    )
    if LINKED_FADING_EXILE_CAPABILITY not in required or active_zone != "battlefield":
        return ()
    if not isinstance(cost_schema, Mapping):
        return ()
    cost = {key: thaw_value(value) for key, value in cost_schema.items() if key != "text"}
    printed_cost = cost_schema.get("text")
    if type(printed_cost) is not str or not printed_cost or cost != _FADE_COST:
        return ()
    return required


def _linked_fading_activation_runtime_dependencies(program, *, mechanics):
    from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors

    required = linked_fading_activation_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=mechanics, active_zone=program.active_zone, cost_schema=program.cost_schema,
    )
    abilities = activated_abilities_from_descriptors(program.handlers)
    if not required or len(program.handlers) != 1 or len(abilities) != 1:
        raise ValueError("A linked Fading activation requires its fixed named cost and activation catalog")
    ability, = abilities
    hand = LINKED_FADING_HAND_BINDING in program.effects[0]
    from ..compiler.activated_costs import activated_ability_cost

    if (ability.sorcery_speed is not hand or ability.zones != ("battlefield",) or ability.mana_ability
            or ability.activation_conditions
            or activated_ability_cost(ability) != thaw_value(program.cost_schema)):
        raise ValueError("A linked Fading activation has the wrong payment or timing")
    return required


def is_closed_linked_fading_activation_program(program):
    if program.event != "activate" or not any(uses_linked_fading_exile(effect) for effect in program.effects):
        return False
    try:
        required = linked_ability_exile_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def linked_ability_exile_runtime_dependencies(program):
    if same_name := _same_name_conditional_exile_runtime_dependencies(program):
        return same_name
    if not _uses_ability_link(program.effects):
        return ()
    effects = program.effects
    mechanics = (LINKED_EXILE_RETURN_MECHANIC, "cr-115-targets", "exile")
    if (len(effects) == 1 and effects[0].get("op") == "offer_optional_effect"
            and set(effects[0]) == {"op", "player", "effects"} and effects[0].get("player") == "$controller"
            and isinstance(effects[0]["effects"], (list, tuple))):
        effects = effects[0]["effects"]
        optional = ("effect.choice.optional_fixed",)
    else:
        optional = ()
    required = linked_ability_exile_node_capabilities(effects=effects, target_schema=program.target_schema, mechanic_ids=mechanics)
    if not required or program.active_zone != "battlefield" or program.event_condition is not None:
        raise ValueError("A linked ability requires one closed source-self instruction")
    if LINKED_FADING_EXILE_CAPABILITY in required:
        is_return = effects[0].get("phase") == "return"
        if optional or program.event != ("permanent.leave.self" if is_return else "activate"):
            raise ValueError("A linked Fading phase requires its matching source ability")
        if not is_return:
            return _linked_fading_activation_runtime_dependencies(program, mechanics=mechanics)
        if program.cost_schema is not None or program.handlers:
            raise ValueError("A linked Fading return requires the unwrapped source-departure trigger")
        return required
    phase = effects[0]["phase"]
    if LINKED_BATCH_DEPARTURE_RETURN_CAPABILITY in required and (optional or program.cost_schema is not None or program.handlers):
        raise ValueError("A linked batch requires its complete unwrapped source trigger")
    if (phase == "exile" and program.event != "permanent.enter.self"
            or phase == "return" and (program.event != "permanent.leave.self" or optional)):
        raise ValueError("The linked ability phase does not match its trigger")
    return (*required, *optional)


def linked_exile_return_node_capabilities(*, effects: Sequence[Mapping[str, Any]], target_schema: Mapping[str, Any] | None, mechanic_ids: Iterable[str]) -> tuple[str, ...]:
    if len(effects) == 1:
        return linked_ability_exile_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    mechanics = set(mechanic_ids)
    if LINKED_EXILE_RETURN_MECHANIC not in mechanics or len(effects) != 2:
        return ()
    first, second = effects
    if set(first) != {"op", "phase", "cards", "binding_id", "spec"} or set(second) != {"op", "phase", "binding_id", "spec"}:
        return ()
    if first.get("op") != LINKED_EXILE_RETURN_OPERATION or second.get("op") != LINKED_EXILE_RETURN_OPERATION or first.get("phase") != "exile" or second.get("phase") != "return" or first.get("spec") != second.get("spec") or first.get("binding_id") != second.get("binding_id"):
        return ()
    if type(first["binding_id"]) is not str or not first["binding_id"].startswith("blink:"):
        return ()
    try:
        spec = LinkedExileReturnSpec.from_dict(first["spec"])
        if spec.announced_opponents:
            if (first["cards"] != "$targets" or thaw_value(target_schema) != announced_opponent_exile_target_schema()
                    or "cr-115-targets" not in mechanics):
                return ()
            from ..target_cardinality import ANY_NUMBER_TARGET_CAPABILITY

            return (LINKED_EXILE_RETURN_CAPABILITY, CONDITIONAL_EXILE_CAPABILITY, ANNOUNCED_OPPONENT_EXILE_CAPABILITY,
                    ANY_NUMBER_TARGET_CAPABILITY, "target.set.one_per_player", "target.revalidate_resolution")
        if spec.same_name_scope is not None:
            if (first["cards"] != "$targets" or thaw_value(target_schema) != same_name_conditional_exile_target_schema()
                    or "cr-115-targets" not in mechanics):
                return ()
            return (LINKED_EXILE_RETURN_CAPABILITY, CONDITIONAL_EXILE_CAPABILITY,
                    SAME_NAME_CONDITIONAL_EXILE_CAPABILITY, "target.revalidate_resolution")
        if target_schema is None:
            if first["cards"] == "$event.permanent_object":
                if spec != LinkedExileReturnSpec(timing="immediate", schema_version=1):
                    return ()
            elif first["cards"] != "$source.zone_object":
                return ()
        else:
            if spec.transformed:
                return ()
            group = TargetGroup.from_mapping(target_schema)
            if first["cards"] != "$targets" or group.zones != ("battlefield",) or group.categories != ("permanent",) or group.max_targets not in {1, 2, 3, 4, 5, 6, 999} or group.min_targets not in {0, group.max_targets}:
                return ()
    except (TypeError, ValueError):
        return ()
    return tuple(sorted({LINKED_EXILE_RETURN_CAPABILITY,
        *((LINKED_TRANSFORMED_RETURN_CAPABILITY,) if spec.transformed else ()),
        *((CONDITIONAL_EXILE_CAPABILITY,) if spec.timing=='source_departure' else ()),
        *(capability for keyword in spec.keywords for capability in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])}))
