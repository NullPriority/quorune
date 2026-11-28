from __future__ import annotations

"""Capability closure for bounded homogeneous target-set effects."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.direct_target import DirectPermanentTargetSpec
from ..compiler.fixed_homogeneous_target_sets import (
    FIXED_HOMOGENEOUS_TARGET_SET_CAPABILITY,
    FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC,
)
from .graveyard_card_targets import (
    GraveyardCardTargetError,
    OwnGraveyardCardTargetSpec,
    PublicGraveyardCardTargetSpec,
)
from .permanent_predicate_capability_shapes import (
    direct_target_predicate_capabilities,
)
from ..tap_state import TAP_TARGET_SET_UNTAP_FIELD
from .tap_state_capability_shapes import target_set_next_untap_dependencies


_OPERATION_CAPABILITIES = {
    "destroy_targets": "permanent.destroy.effect",
    "exile_permanent_targets": "permanent.exile.effect",
    "return_permanent_targets_to_owner_hand": (
        "permanent.return.owner_hand"
    ),
    "return_graveyard_targets_to_owner_hand": (
        "card.return.own_graveyard_to_owner_hand"
    ),
    "exile_public_graveyard_targets": "card.exile.public_graveyard",
    "tap_targets": "permanent.tap.effect",
    "untap_targets": "permanent.untap.effect",
}
_OPERATION_MECHANICS = {
    "destroy_targets": "destroy",
    "exile_permanent_targets": "exile",
    "return_permanent_targets_to_owner_hand": "return-to-owner-hand",
    "return_graveyard_targets_to_owner_hand": "return-to-owner-hand",
    "exile_public_graveyard_targets": "exile",
    "tap_targets": "tap-and-untap",
    "untap_targets": "tap-and-untap",
}


OPTIONAL_GRAVEYARD_TARGET_RETURN_VERSION = 3


def validate_optional_graveyard_target_return(effect, *, player, resolved=False):
    children = effect.get("effects")
    if (
        set(effect) != {"op", "player", "effects", "schema_version"}
        or effect["op"] != "offer_optional_effect"
        or effect["player"] != player
        or type(effect["schema_version"]) is not int
        or effect["schema_version"] != OPTIONAL_GRAVEYARD_TARGET_RETURN_VERSION
        or not isinstance(children, (list, tuple))
        or len(children) != 1
        or not isinstance(children[0], Mapping)
    ):
        raise ValueError("Expected one versioned optional graveyard return")
    child = children[0]
    if (
        set(child) != {"op", "cards", "maximum_targets"}
        or child["op"] != "return_graveyard_targets_to_owner_hand"
        or type(child["maximum_targets"]) is not int
        or child["maximum_targets"] != 1
    ):
        raise ValueError("Expected the original own-graveyard return instruction")
    cards = child["cards"]
    if resolved:
        if (not isinstance(cards, (list, tuple)) or len(cards) > 1
                or any(type(ref) is not str or not ref for ref in cards)):
            raise ValueError("Expected zero or one revalidated graveyard card")
    elif cards != "$targets":
        raise ValueError("Expected the announced graveyard target set")


def _target_bounds(schema: Mapping[str, Any]) -> tuple[int, int] | None:
    count_fields = set(schema).intersection({"count", "min", "max", "up_to"})
    if count_fields == {"count"}:
        count = schema["count"]
        return (
            (count, count)
            if type(count) is int and 2 <= count <= 6
            else None
        )
    if count_fields == {"min", "max"}:
        minimum = schema["min"]
        maximum = schema["max"]
        return (
            (minimum, maximum)
            if minimum == 1 and maximum == 2
            else None
        )
    if count_fields == {"up_to"}:
        maximum = schema["up_to"]
        return (
            (0, maximum)
            if type(maximum) is int and 1 <= maximum <= 6
            else None
        )
    return None


def _singular_schema(
    target_schema: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], bool] | None:
    if not isinstance(target_schema, Mapping):
        return None
    schema = dict(target_schema)
    bounds = _target_bounds(schema)
    if bounds is None:
        return None
    same_owner = schema.pop("same_owner", False)
    if type(same_owner) is not bool:
        return None
    for field in ("count", "min", "max", "up_to"):
        schema.pop(field, None)
    schema["count"] = 1
    return schema, same_owner


def fixed_homogeneous_target_set_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    if any('target_roles' in effect for effect in effects):
        from .compound_target_sets import compound_target_set_node_capabilities
        return compound_target_set_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {
        FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC,
        "cr-115-targets",
    }.issubset(mechanics) or len(effects) != 1:
        return ()
    effect = effects[0]
    from .event_player_targets import DAMAGED_PLAYER_GRAVEYARD_EXILE_CAPABILITY, is_damaged_player_graveyard_exile

    event_owner = ()
    if isinstance(target_schema, Mapping) and 'owner_seat' in target_schema:
        if 'followup' in effect:
            from .targeted_graveyard_outcomes import targeted_graveyard_outcome_node_capabilities

            return targeted_graveyard_outcome_node_capabilities(effects=effects, target_schema=target_schema)
        if not is_damaged_player_graveyard_exile(target_schema, effects):
            return ()
        target_schema = {key: value for key, value in target_schema.items() if key != 'owner_seat'}
        event_owner = (DAMAGED_PLAYER_GRAVEYARD_EXILE_CAPABILITY,)
    from .announced_destruction_controller_tokens import (
        optional_graveyard_controller_token_dependencies, uses_optional_graveyard_controller_tokens,
    )

    if uses_optional_graveyard_controller_tokens(effect):
        try:
            return optional_graveyard_controller_token_dependencies(effect, target_schema)
        except (KeyError, TypeError, ValueError):
            return ()
    operation = effect.get("op")
    maximum = effect.get("maximum_targets")
    from .affected_library_search_batch import (
        EXILE_LAND_SEARCH_FIELD, EXILE_LAND_SEARCH_MECHANIC, exile_land_payload_dependencies, exile_land_target_schema,
    )

    if EXILE_LAND_SEARCH_FIELD in effect:
        if (EXILE_LAND_SEARCH_MECHANIC not in mechanics or target_schema != exile_land_target_schema()
                or type(target_schema.get("count")) is not int):
            return ()
        try:
            result = exile_land_payload_dependencies(effect)
        except ValueError:
            return ()
        return (FIXED_HOMOGENEOUS_TARGET_SET_CAPABILITY, _OPERATION_CAPABILITIES["exile_permanent_targets"],
                *direct_target_predicate_capabilities(target_schema), "target.revalidate_resolution", *result)
    if operation == 'exile_permanent_targets' and 'followup' in effect:
        from .targeted_exile_token_results import targeted_exile_token_node_capabilities
        return targeted_exile_token_node_capabilities(effect, target_schema, mechanics)
    if operation == 'exile_public_graveyard_targets' and 'followup' in effect:
        from .targeted_graveyard_outcomes import targeted_graveyard_outcome_node_capabilities

        return targeted_graveyard_outcome_node_capabilities(effects=effects, target_schema=target_schema)
    from .reanimation_followup import uses_returned_creature_counters
    if uses_returned_creature_counters(effect):
        return returned_creature_counter_capabilities(effect, target_schema, mechanics)
    if operation=='reanimate_targets':
        from ..compiler.reanimation_templates import (FIXED_TARGET_REANIMATION_SET_CAPABILITY,
            FIXED_TARGET_REANIMATION_SET_MECHANIC)
        from .reanimation_capability_shapes import fixed_target_reanimation_node_capabilities
        if (FIXED_TARGET_REANIMATION_SET_MECHANIC not in mechanics
                or set(effect)!={'op','cards','maximum_targets','schema_version','battlefield_controller','battlefield_tapped'}
                or effect.get('cards')!='$targets' or type(maximum) is not int or not 2<=maximum<=6
                or type(effect.get('schema_version')) is not int or effect['schema_version']!=1
                or effect.get('battlefield_controller') not in ('actor','owner')
                or type(effect.get('battlefield_tapped')) is not bool):return ()
        normalized=_singular_schema(target_schema);bounds=_target_bounds(dict(target_schema or {}))
        if normalized is None or bounds is None or bounds[1]!=maximum:return ()
        singular,_same_owner=normalized
        scalar={'op':'reanimate','card':'$target.0','controller':
            '$target.owner.0' if effect['battlefield_controller']=='owner' else '$controller',
            'tapped':effect['battlefield_tapped']}
        owned=fixed_target_reanimation_node_capabilities(effects=(scalar,),target_schema=singular,mechanic_ids=mechanics)
        return (FIXED_HOMOGENEOUS_TARGET_SET_CAPABILITY,FIXED_TARGET_REANIMATION_SET_CAPABILITY,*owned,
            'target.revalidate_resolution') if owned else ()
    try:
        next_untap = target_set_next_untap_dependencies(effect)
    except ValueError:
        return ()
    expected_fields = {"op", "cards", "maximum_targets"}
    if next_untap:
        expected_fields.add(TAP_TARGET_SET_UNTAP_FIELD)
    if (
        operation not in _OPERATION_CAPABILITIES
        or _OPERATION_MECHANICS[str(operation)] not in mechanics
        or set(effect) != expected_fields
        or effect.get("cards") != "$targets"
        or type(maximum) is not int
        or not 1 <= maximum <= 6
    ):
        return ()
    normalized = _singular_schema(target_schema)
    if normalized is None:
        return ()
    singular, same_owner = normalized
    bounds = _target_bounds(dict(target_schema or {}))
    if bounds is None or bounds[1] != maximum:
        return ()

    predicate_capabilities: tuple[str, ...] = ()
    if operation in {
        "destroy_targets",
        "exile_permanent_targets",
        "return_permanent_targets_to_owner_hand",
        "tap_targets",
        "untap_targets",
    }:
        if same_owner:
            return ()
        try:
            DirectPermanentTargetSpec.from_target_schema(singular)
        except (TypeError, ValueError):
            return ()
        predicate_capabilities = direct_target_predicate_capabilities(singular)
    elif operation == "return_graveyard_targets_to_owner_hand":
        if same_owner:
            return ()
        try:
            OwnGraveyardCardTargetSpec.from_target_schema(singular)
        except (GraveyardCardTargetError, TypeError):
            return ()
    else:
        try:
            PublicGraveyardCardTargetSpec.from_target_schema(singular)
        except (GraveyardCardTargetError, TypeError):
            return ()

    return (
        FIXED_HOMOGENEOUS_TARGET_SET_CAPABILITY,
        _OPERATION_CAPABILITIES[str(operation)],
        *next_untap,
        *predicate_capabilities,
        *event_owner,
        "target.revalidate_resolution",
    )


def returned_creature_counter_capabilities(effect, target_schema, mechanics):
    from ..replacement.immutable import FrozenMap
    from .reanimation_followup import RETURNED_CREATURE_COUNTER_CAPABILITY, RETURNED_CREATURE_COUNTER_VERSION

    if not isinstance(target_schema, Mapping):
        return ()
    numeric = target_schema.get("numeric_characteristic")
    if not isinstance(numeric, Mapping) or type(numeric.get("value")) is not int or numeric["value"] < 0:
        return ()
    expected = {
        "zones": ["graveyard"], "categories": ["card"], "owner_relation": "you",
        "types_any": ["creature"], "up_to": 2,
        "numeric_characteristic": {"characteristic": "power", "comparison": "at_most", "value": numeric["value"]},
    }
    if (FrozenMap(target_schema) != FrozenMap(expected) or type(target_schema["up_to"]) is not int
            or type(effect["schema_version"]) is not int or effect["schema_version"] != RETURNED_CREATURE_COUNTER_VERSION
            or effect.get("battlefield_controller") != "actor" or effect.get("battlefield_tapped") is not False):
        return ()
    primitive = {**effect, "schema_version": 1}
    owned = fixed_homogeneous_target_set_node_capabilities(effects=(primitive,),
        target_schema=target_schema, mechanic_ids=mechanics)
    return (*owned, RETURNED_CREATURE_COUNTER_CAPABILITY, "counter.producer.fixed_permanent_target_set_effect",
            "counter.placement.quantity_replacement") if owned else ()


def is_closed_fixed_homogeneous_target_set_program(program: Any) -> bool:
    """Recognize one capability-closed bounded homogeneous target set."""

    required = set(
        fixed_homogeneous_target_set_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def announced_target_set_node_capabilities(
    *, effects: Sequence[Mapping[str, Any]], target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Validate variable cardinality before projecting to the fixed owner."""
    from ..compiler.announced_target_sets import ANNOUNCED_TARGET_SET_CAPABILITY, ANNOUNCED_TARGET_SET_MECHANIC
    from ..query_effect_amount_model import CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY
    from ..target_cardinality import bind_announced_target_schema
    mechanics = set(mechanic_ids)
    if ANNOUNCED_TARGET_SET_MECHANIC not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    from .announced_x_damage_sets import SCALED_ANNOUNCED_TARGET_DAMAGE_FIELD, scaled_announced_target_damage_node_capabilities

    if SCALED_ANNOUNCED_TARGET_DAMAGE_FIELD in effect:
        return scaled_announced_target_damage_node_capabilities(effects, target_schema, mechanics)
    from ..phasing_model import PHASE_OUT_OPERATION, PHASE_OUT_TARGET_SET_SUBJECT
    if effect.get("op") == PHASE_OUT_OPERATION and effect.get("subject") == PHASE_OUT_TARGET_SET_SUBJECT:
        from .occurrence_object_results import phase_out_node_capabilities
        return phase_out_node_capabilities(effects, target_schema)
    from .target_characteristic_sets import OPERATION, target_characteristic_set_capabilities
    characteristic = effect.get('op') == OPERATION
    fields = {"op", "cards", "maximum_targets", "announced_target_count"}
    if effect.get("op") == "exile_permanent_targets" and "followup" in effect:
        fields.add("followup")
    if characteristic:
        fields.update(('schema_version', 'power', 'toughness', 'keywords'))
    if (set(effect) != fields
            or effect.get("announced_target_count") is not True):
        return ()
    try:
        maximum = CastXAmountSpec.from_dict(effect["maximum_targets"])
        if maximum.coefficient != 1 or not isinstance(target_schema, Mapping) or "count_parameter" not in target_schema:
            return ()
        schema = bind_announced_target_schema(target_schema, 2)
    except (TypeError, ValueError):
        return ()
    fixed = {key: value for key, value in effect.items() if key != "announced_target_count"}
    fixed["maximum_targets"] = 2
    owner = target_characteristic_set_capabilities if characteristic else fixed_homogeneous_target_set_node_capabilities
    owned = owner(effects=(fixed,), target_schema=schema,
        mechanic_ids=mechanics | {FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC})
    return (*owned, ANNOUNCED_TARGET_SET_CAPABILITY, DECLARED_AMOUNT_CAPABILITY) if owned else ()


def announced_target_runtime_dependencies(program: Any) -> tuple[str, ...]:
    """Require the new authority even if a producer omits its annotation."""
    from .reanimation_followup import uses_returned_creature_counters

    if any(uses_returned_creature_counters(effect) for effect in program.effects):
        from ..compiler.reanimation_templates import FIXED_TARGET_REANIMATION_MECHANIC, FIXED_TARGET_REANIMATION_SET_MECHANIC

        mechanics = (*program.coverage, FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC,
                     FIXED_TARGET_REANIMATION_MECHANIC, FIXED_TARGET_REANIMATION_SET_MECHANIC, "cr-115-targets")
        required = fixed_homogeneous_target_set_node_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=mechanics,
        )
        if not required:
            raise ValueError("Returned creature counters require their original target-set reanimation")
        return required
    from .targeted_control_set import targeted_control_set_runtime_dependencies
    from .counter_target_set_shapes import split_two_counter_program_dependencies
    from .controlled_destruction_source_counters import controlled_destruction_source_counter_program_dependencies

    from .announced_x_damage_sets import scaled_announced_target_damage_program_dependencies

    if scaled_damage := scaled_announced_target_damage_program_dependencies(program):
        return scaled_damage
    if required := controlled_destruction_source_counter_program_dependencies(program):
        return required
    if split := split_two_counter_program_dependencies(program):
        return split
    if targeted_control := targeted_control_set_runtime_dependencies(program):
        return targeted_control
    schema = getattr(program, "target_schema", None)
    parameter = schema.get('count_parameter') if isinstance(schema,Mapping) else None
    if isinstance(parameter,Mapping) and parameter.get('parameter')=='any_number':
        from .any_number_target_shapes import any_number_target_node_capabilities
        from .monarch_selected_player_mill import monarch_selected_player_mill_runtime_dependencies

        if monarch_mill := monarch_selected_player_mill_runtime_dependencies(program):
            return monarch_mill
        owned=any_number_target_node_capabilities(effects=program.effects,target_schema=schema,mechanic_ids=program.coverage)
        if not owned:raise ValueError('Any-number target result or schema is malformed')
        return owned
    parameterized = isinstance(schema, Mapping) and "count_parameter" in schema
    from ..phasing_model import PHASE_OUT_OPERATION
    from .target_characteristic_sets import OPERATION
    effects = tuple(effect for effect in program.effects if effect.get("op") in {*_OPERATION_CAPABILITIES, OPERATION, PHASE_OUT_OPERATION}
        and ("announced_target_count" in effect or isinstance(effect.get("maximum_targets"), Mapping)))
    if not parameterized and not effects:
        return ()
    if not parameterized or not effects:
        raise ValueError("Announced target effects require their declared target-count schema")
    required = set()
    for effect in effects:
        owned = announced_target_set_node_capabilities(effects=(effect,), target_schema=schema,
            mechanic_ids=program.coverage)
        if not owned:
            raise ValueError("Announced target-count effect or schema is malformed")
        required.update(owned)
    return tuple(sorted(required))


def is_closed_announced_target_set_program(program: Any) -> bool:
    event = getattr(program,"event",None)
    spell = str(getattr(program,"ability_id","")).startswith("spell:") and event == "resolve"
    if not spell:
        if event != "activate":return False
        from ..activation_x import activation_x_symbols, activation_x_capabilities
        cost = program.cost_schema or {}
        from ..colored_hand_reveal_model import (
            VARIABLE_COLORED_REVEAL_PRICE_FIELD, variable_colored_reveal_program_dependencies,
        )

        if VARIABLE_COLORED_REVEAL_PRICE_FIELD in cost:
            try:
                price_dependencies = variable_colored_reveal_program_dependencies(program)
            except (TypeError, ValueError):
                return False
            if not price_dependencies or not set(price_dependencies).issubset(program.capability_dependencies):
                return False
        else:
            if (type(cost.get("announced_x_symbols")) is not int
                    or not set(activation_x_capabilities(symbols=cost.get('announced_x_symbols'),
                        sacrifice_source=cost.get('sacrifice_source',False),
                        discard_cards=bool(cost.get('choices')),
                        discard_source=cost.get('discard_source', False))).issubset(program.capability_dependencies)
                    or activation_x_symbols(str(cost.get("text") or ""),
                        allow_source_sacrifice=cost.get('sacrifice_source') is True,
                        allow_fixed_discard=bool(cost.get('choices')),
                        allow_source_discard=cost.get('discard_source') is True) != cost["announced_x_symbols"]):
                return False
    required = announced_target_set_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)


__all__ = [
    "fixed_homogeneous_target_set_node_capabilities",
    "is_closed_fixed_homogeneous_target_set_program",
]
