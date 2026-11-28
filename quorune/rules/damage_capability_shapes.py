from __future__ import annotations

"""Closed capability shapes for fixed positive damage instructions."""

from typing import Any, Iterable, Mapping, Sequence

from ..announced_damage import (
    ANNOUNCED_DAMAGE_CAPABILITY,
    ANNOUNCED_DAMAGE_EFFECT_FIELD,
    DAMAGE_ALLOCATION_FIELD,
    TURN_DAMAGE_MAXIMUM,
    VariableDamageAllocationSpec,
    damage_allocation_spec,
    validate_announced_damage_effect,
    variable_damage_allocation,
)
from ..compiler.direct_target import DirectPermanentTargetSpec
from ..damage_prevention_permission import (
    TEMPORARY_DAMAGE_PROHIBITION_FIELD, UNPREVENTABLE_CAPABILITY,
    validate_temporary_damage_prevention_prohibition,
)
from ..life_gain_permission import LIFE_GAIN_PROHIBITION_CAPABILITY, validate_temporary_life_gain_effect
from ..replacement.immutable import FrozenMap, thaw_value
from ..damage_death_exile_model import (
    DAMAGE_DEATH_EXILE_CAPABILITY, DAMAGE_DEATH_EXILE_FIELD, DAMAGE_DEATH_EXILE_MECHANIC,
    DamageDeathExileScope, damage_death_exile_scope,
)
from ..fixed_damage_set_model import (
    FixedDamageSetError,
    FixedDamageSetSpec,
    PermanentControllerRelation,
    PermanentDamageGroup,
    PlayerDamageGroup, DEALT_LIFE_GAIN_FIELD, DEALT_LIFE_GAIN_CAPABILITY,
    TARGETED_DEALT_LIFE_GAIN_VERSION, dealt_life_gain_group_is_closed,
)
from .permanent_predicate_capability_shapes import (
    direct_permanent_target_schema_is_closed,
    direct_target_predicate_capabilities,
)


NONCOMBAT_PREVENTION_LIFE_GAIN_CAPABILITY = "damage.prevention.static_noncombat_life_gain"
NONCOMBAT_PREVENTION_LIFE_GAIN_HANDLER = "prevention.damage.noncombat_life_gain.v1"
NONCOMBAT_PREVENTION_LIFE_GAIN_TEMPLATE = "noncombat-prevention-controller-life-gain-v1"


def noncombat_prevention_life_gain_descriptor() -> dict[str, Any]:
    return {
        "handler_id": NONCOMBAT_PREVENTION_LIFE_GAIN_HANDLER,
        "schema_version": 1,
        "event": "damage",
        "condition": {"damage_kind": "noncombat", "recipient": "controller"},
        "modification": {"amount": "all", "life_gain": "prevented_amount"},
    }


def noncombat_prevention_life_runtime_dependencies(program):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE

    handlers = program.handlers
    if not (
        NONCOMBAT_PREVENTION_LIFE_GAIN_TEMPLATE in str(program.provenance.get("template_id") or "").split("+")
        or any(handler.get("handler_id") == NONCOMBAT_PREVENTION_LIFE_GAIN_HANDLER for handler in handlers)
    ):
        return ()
    if (
        (program.event, program.active_zone) != ("damage", "battlefield")
        or program.target_schema is not None or program.cost_schema is not None
        or program.event_condition is not None or program.effects
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or len(handlers) != 1 or dict(handlers[0]) != noncombat_prevention_life_gain_descriptor()
    ):
        raise ValueError("Intrinsic prevention life gain requires a current battlefield ability and its complete result")
    return (NONCOMBAT_PREVENTION_LIFE_GAIN_CAPABILITY,)


CONTROLLER_DAMAGE_LIFE_FLOOR_HANDLER = "replacement.damage.result.controller_life_floor.v2"
CONTROLLER_DAMAGE_LIFE_FLOOR_TEMPLATE = "damage-result-controller-life-floor-v2"
DAMAGE_RESULT_REPLACEMENT_CAPABILITY = "damage.result.replacement_order"


def controller_damage_result_life_floor_descriptor() -> dict[str, Any]:
    return {
        "handler_id": CONTROLLER_DAMAGE_LIFE_FLOOR_HANDLER, "schema_version": 2,
        "event": "damage.results",
        "condition": {"affected_player_relation": "source_controller", "requires_controlled_creature": False},
        "modification": {"minimum_life": 1},
    }


def controller_damage_result_life_floor_runtime_dependencies(program):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE

    handlers = program.handlers
    if not (
        CONTROLLER_DAMAGE_LIFE_FLOOR_TEMPLATE in str(program.provenance.get("template_id") or "").split("+")
        or any(handler.get("handler_id") == CONTROLLER_DAMAGE_LIFE_FLOOR_HANDLER for handler in handlers)
    ):
        return ()
    if (
        (program.event, program.active_zone) != ("damage.results", "battlefield")
        or program.target_schema is not None or program.cost_schema is not None
        or program.event_condition is not None or program.effects
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or len(handlers) != 1 or dict(handlers[0]) != controller_damage_result_life_floor_descriptor()
    ):
        raise ValueError("Controller life floor requires its exact current battlefield damage-result ability")
    return (DAMAGE_RESULT_REPLACEMENT_CAPABILITY,)


SOURCE_RELATIVE_DAMAGE_VERSION_FIELD = "source_relative_damage_version"
SOURCE_SELF_DAMAGE_TEMPLATE = "damage-source-self-one-v1"


def source_self_damage_effect() -> dict[str, Any]:
    return {"op": "damage", "source": "$source", "target": "$source.zone_object",
            "amount": 1, SOURCE_RELATIVE_DAMAGE_VERSION_FIELD: 1}


def linked_player_received_damage_effect() -> dict[str, Any]:
    from ..entry_designations import LINKED_RECEIVED_DAMAGE_PLAYER_FIELD
    from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    return {
        "op": "damage", "source": "$source", "target": "$context." + LINKED_RECEIVED_DAMAGE_PLAYER_FIELD,
        "amount": ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT).to_dict(),
        SOURCE_RELATIVE_DAMAGE_VERSION_FIELD: 1,
    }


def linked_player_received_damage_condition() -> dict[str, str]:
    return {"field": "target", "op": "eq", "value": "$source.ref"}


def source_relative_damage_role(effect) -> str:
    from ..entry_designations import LINKED_RECEIVED_DAMAGE_PLAYER_FIELD
    from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    if type(effect.get(SOURCE_RELATIVE_DAMAGE_VERSION_FIELD)) is not int:
        raise ValueError("Source-relative damage requires an explicit operation version")
    if effect.get("target") == "$source.zone_object" and type(effect.get("amount")) is int:
        if thaw_value(FrozenMap(effect)) == source_self_damage_effect():
            return "source"
    if effect.get("target") == "$context." + LINKED_RECEIVED_DAMAGE_PLAYER_FIELD:
        amount = effect.get("amount")
        if (isinstance(amount, Mapping)
                and ScalarEffectAmountSpec.from_dict(amount) == ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT)
                and thaw_value(FrozenMap(effect)) == linked_player_received_damage_effect()):
            return "linked_player"
    raise ValueError("Source-relative damage requires its complete positive amount and recipient")


def _source_relative_damage_dependencies(role):
    common = ("damage.amount.positive", "damage.source.stack_current_or_lki")
    if role == "source":
        return (*common, "damage.result.multitype_permanent")
    from ..entry_designations import ENTRY_DESIGNATION_CAPABILITY
    from ..received_damage_model import RECEIVED_DAMAGE_CAPABILITY
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY

    return (*common, "damage.result.player_life", ENTRY_DESIGNATION_CAPABILITY, RECEIVED_DAMAGE_CAPABILITY, SCALAR_AMOUNT_CAPABILITY)


def source_relative_damage_runtime_dependencies(program):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..entry_designations import LINKED_PLAYER_RECEIVED_DAMAGE_PROGRAM_TEMPLATE
    from .effect_shape_walkers import contains_nested_key

    template = str(program.provenance.get("template_id") or "")
    marked = {SOURCE_SELF_DAMAGE_TEMPLATE, LINKED_PLAYER_RECEIVED_DAMAGE_PROGRAM_TEMPLATE}.intersection(template.split("+"))
    if not marked and not contains_nested_key((program.effects, program.handlers), SOURCE_RELATIVE_DAMAGE_VERSION_FIELD):
        return ()
    if program.active_zone != "battlefield" or program.target_schema is not None or len(program.effects) != 1:
        raise ValueError("Source-relative damage requires one untargeted battlefield program")
    role = source_relative_damage_role(program.effects[0])
    if role == "source":
        from ..semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID

        if (
            program.event != "activate" or program.event_condition is not None
            or not isinstance(program.cost_schema, Mapping) or program.cost_schema.get("tap_source") is not True
            or len(program.handlers) != 1 or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID
        ):
            raise ValueError("One source self-damage requires its ordinary battlefield tap activation")
    elif (
        program.event != "damage.received" or program.cost_schema is not None or program.handlers
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or thaw_value(program.event_condition) != linked_player_received_damage_condition()
    ):
        raise ValueError("Linked player damage requires the current original receiving occurrence")
    return _source_relative_damage_dependencies(role)


TARGETED_DEALT_LIFE_GAIN_CAPABILITY = "damage.target.dealt_life_gain"


_FIXED_DAMAGE_TARGET_SCHEMAS: dict[str, Mapping[str, Any]] = {
    "any_target": {
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "damageable",
        "count": 1,
    },
    "creature": {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "types_any": ["creature"],
        "count": 1,
    },
    "creature_or_planeswalker": {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "types_any": ["creature", "planeswalker"],
        "count": 1,
    },
    "player_or_planeswalker": {
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "player_or_planeswalker",
        "count": 1,
    },
    "opponent_or_planeswalker": {
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "player_or_planeswalker",
        "count": 1,
        "player_relation": "opponent",
    },
    "player": {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
    },
    "opponent": {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
        "player_relation": "opponent",
    },
}
_PLAYER_DAMAGE_DOMAINS = frozenset(
    {
        "any_target",
        "player_or_planeswalker",
        "opponent_or_planeswalker",
        "player",
        "opponent",
    }
)
_PERMANENT_DAMAGE_DOMAINS = frozenset(
    {
        "any_target",
        "creature",
        "creature_or_planeswalker",
        "player_or_planeswalker",
        "opponent_or_planeswalker",
    }
)


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _fixed_set_damage_capabilities(
    effect: Mapping[str, Any],
    target_schema: Mapping[str, Any] | None,
    mechanics: set[str],
) -> tuple[str, ...] | None:
    from .aggregated_arrival_damage import artifact_arrival_damage_capabilities, uses_artifact_arrival_damage
    from .defending_creature_damage import defending_creature_damage_capabilities, is_defending_creature_damage
    from .random_opponent_damage import RANDOM_OPPONENT_DAMAGE_FIELD, random_opponent_damage_capabilities

    if uses_artifact_arrival_damage(effect):
        return artifact_arrival_damage_capabilities(effect, target_schema, mechanics)
    if RANDOM_OPPONENT_DAMAGE_FIELD in effect:
        return random_opponent_damage_capabilities(effect, target_schema)
    if DEALT_LIFE_GAIN_FIELD in effect:
        if (target_schema is not None or type(effect[DEALT_LIFE_GAIN_FIELD]) is not int
                or effect[DEALT_LIFE_GAIN_FIELD] != 1):
            return ()
        try:
            if not dealt_life_gain_group_is_closed(FixedDamageSetSpec.from_dict({"groups": effect.get("groups")})):
                return ()
        except FixedDamageSetError:
            return ()
        plain = {key: value for key, value in effect.items() if key != DEALT_LIFE_GAIN_FIELD}
        base = _fixed_set_damage_capabilities(plain, target_schema, mechanics)
        return (*base, DEALT_LIFE_GAIN_CAPABILITY, "life.change.effect") if base else ()
    if is_defending_creature_damage(effect):
        return defending_creature_damage_capabilities(effect, target_schema)
    if not (
        set(effect) == {"op", "source", "amount", "groups"}
        and effect.get("op") == "damage_fixed_set"
        and effect.get("source") == "$source"
        and _positive_int(effect.get("amount"))
    ):
        return None
    try:
        spec = FixedDamageSetSpec.from_dict({"groups": effect.get("groups")})
    except FixedDamageSetError:
        return ()
    targeted = any(
        isinstance(group, PermanentDamageGroup)
        and group.controller_relation is PermanentControllerRelation.TARGET_PLAYER
        for group in spec.groups
    )
    player_targets = (
        {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
        },
        {
            "zones": ["player"],
            "categories": ["player"],
            "player_relation": "opponent",
            "count": 1,
        },
    )
    valid_target = (
        not targeted and target_schema is None
        or targeted and dict(target_schema or {}) in player_targets
    )
    if not valid_target or targeted and "cr-115-targets" not in mechanics:
        return ()
    dependencies = {"damage.amount.positive", "damage.batch.fixed_set", "damage.source.stack_current_or_lki"}
    if any(isinstance(group, PlayerDamageGroup) for group in spec.groups):
        dependencies.add("damage.result.player_life")
    permanent_groups = tuple(
        group for group in spec.groups if isinstance(group, PermanentDamageGroup)
    )
    if permanent_groups:
        dependencies.add("damage.result.multitype_permanent")
    if targeted:
        dependencies.add("target.revalidate_resolution")
    if any(_uses_characteristic_predicate(group) for group in permanent_groups):
        dependencies.add("target.permanent.characteristic_predicate")
    if any(group.query.state_predicate is not None for group in permanent_groups):
        dependencies.add("state_query.permanent.public_state_predicate")
    return tuple(sorted(dependencies))


def _uses_characteristic_predicate(group: PermanentDamageGroup) -> bool:
    query = group.query
    return bool(
        query.excluded_types
        or query.subtypes_any
        or query.excluded_subtypes
        or query.supertypes_all
        or query.colors_all
        or query.colors_any
        or query.colorless is not None
        or query.minimum_color_count is not None
        or query.keywords_all
        or query.keywords_none
        or query.token is not None
        or group.exclude_source
    )


def _fixed_direct_damage_capabilities(
    effect: Mapping[str, Any],
    target_schema: Mapping[str, Any] | None,
    mechanics: set[str],
) -> tuple[str, ...]:
    if (
        "cr-115-targets" not in mechanics
        or set(effect) != {"op", "source", "target", "amount"}
        or effect.get("op") != "damage"
        or effect.get("source") != "$source"
        or effect.get("target") != "$target.0"
        or not _positive_int(effect.get("amount"))
    ):
        return ()
    schema = dict(target_schema or {})
    domain = next(
        (
            name
            for name, expected in _FIXED_DAMAGE_TARGET_SCHEMAS.items()
            if schema == expected
        ),
        None,
    )
    direct_target = None
    if domain is None and direct_permanent_target_schema_is_closed(schema):
        direct_target = DirectPermanentTargetSpec.from_target_schema(schema)
    if domain is None and direct_target is None:
        return ()
    dependencies = {"damage.amount.positive", "damage.source.stack_current_or_lki"}
    if domain in _PLAYER_DAMAGE_DOMAINS:
        dependencies.add("damage.result.player_life")
    if domain in _PERMANENT_DAMAGE_DOMAINS or direct_target is not None:
        dependencies.add("damage.result.multitype_permanent")
    if direct_target is not None:
        dependencies.update(direct_target_predicate_capabilities(schema))
        dependencies.add("target.revalidate_resolution")
    else:
        dependencies.add(
            "target.public.player_or_damageable_permanent"
            if domain == "any_target"
            else "target.revalidate_resolution"
        )
    return tuple(sorted(dependencies))


def _targeted_dealt_life_gain_effect(effect):
    return (
        set(effect) == {"op", "source", "target", "amount", DEALT_LIFE_GAIN_FIELD}
        and effect.get(DEALT_LIFE_GAIN_FIELD) == TARGETED_DEALT_LIFE_GAIN_VERSION
        and type(effect[DEALT_LIFE_GAIN_FIELD]) is int
        and effect.get("op") == "damage"
        and effect.get("source") == "$source"
        and effect.get("target") == "$target.0"
    )


def targeted_dealt_life_gain_amount(effect, target_schema):
    """Identify the two typed damage amounts admitted by the targeted result."""
    if not _targeted_dealt_life_gain_effect(effect):
        return None
    from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
    from ..landwalk_vocabulary import BASIC_LAND_TYPES
    from ..object_predicate import ObjectQuerySpec
    from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_KIND, PublicQueryAmountSpec
    from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    schema = thaw_value(target_schema)
    amount = effect["amount"]
    if not isinstance(amount, Mapping):
        return None
    if schema == _FIXED_DAMAGE_TARGET_SCHEMAS["player_or_planeswalker"]:
        try:
            paid = ScalarEffectAmountSpec.from_dict(thaw_value(amount))
        except ValueError:
            return None
        power = ScalarEffectAmountSpec(
            ScalarAmountOrigin.PAID_SACRIFICE_CREATURE,
            characteristic="power", schema_version=9,
        )
        return paid if paid == power else None
    if schema != _FIXED_DAMAGE_TARGET_SCHEMAS["any_target"]:
        return None
    if amount.get("kind") != PUBLIC_QUERY_AMOUNT_KIND:
        return None
    try:
        count = PublicQueryAmountSpec.from_dict(thaw_value(amount))
    except ValueError:
        return None
    quantity = count.quantity
    if not isinstance(quantity, CharacteristicQuantitySpec) or quantity.query is None:
        return None
    subtypes = quantity.query.subtypes_all
    if len(subtypes) != 1 or subtypes[0] not in BASIC_LAND_TYPES:
        return None
    expected = PublicQueryAmountSpec(CharacteristicQuantitySpec(
        scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
        query=ObjectQuerySpec(zones=("battlefield",), types_all=("land",), subtypes_all=subtypes),
    ))
    return count if count == expected else None


def _targeted_dealt_life_gain_capabilities(effect, target_schema, mechanics):
    if not _targeted_dealt_life_gain_effect(effect) or target_schema not in (
        _FIXED_DAMAGE_TARGET_SCHEMAS["any_target"],
        _FIXED_DAMAGE_TARGET_SCHEMAS["player_or_planeswalker"],
    ):
        return ()
    body = {key: value for key, value in effect.items() if key != DEALT_LIFE_GAIN_FIELD}
    base = _fixed_direct_damage_capabilities(body, target_schema, mechanics)
    return (*base, TARGETED_DEALT_LIFE_GAIN_CAPABILITY, "life.change.effect") if base else ()


def _targeted_dealt_life_gain_runtime_dependencies(program):
    from .casting_additional_costs import fixed_creature_sacrifice_price
    from ..query_effect_amount_model import PublicQueryAmountSpec
    from ..scalar_effect_amount_model import ScalarEffectAmountSpec

    effect = program.effects[0]
    schema = thaw_value(program.target_schema)
    amount = targeted_dealt_life_gain_amount(effect, schema)
    if (
        (program.event, program.active_zone) != ("resolve", "stack")
        or program.handlers
        or not (
            isinstance(amount, PublicQueryAmountSpec) and program.cost_schema is None
            or isinstance(amount, ScalarEffectAmountSpec) and fixed_creature_sacrifice_price(program.cost_schema)
        )
    ):
        raise ValueError("Targeted dealt-damage life gain requires its exact spell price, target and amount")
    return _targeted_dealt_life_gain_capabilities(
        {**effect, "amount": 1}, schema, {"cr-115-targets"},
    )


def dealt_damage_life_gain_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(DEALT_LIFE_GAIN_FIELD in effect for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if len(program.effects) == 1 and program.effects[0].get(DEALT_LIFE_GAIN_FIELD) == TARGETED_DEALT_LIFE_GAIN_VERSION:
        return _targeted_dealt_life_gain_runtime_dependencies(program)
    own_upkeep = {"all": [{"field": "player", "op": "eq", "value": "$source.controller"},
                          {"field": "step", "op": "eq", "value": "upkeep"}]}
    carrier = (program.event, program.active_zone)
    if (len(program.effects) != 1 or program.cost_schema is not None or program.handlers
            or carrier != ("resolve", "stack")
            and (carrier != ("step.begin", "battlefield") or program.event_condition != own_upkeep)):
        raise ValueError("Dealt damage life gain requires its exact ordinary spell or source upkeep")
    required = _fixed_set_damage_capabilities(program.effects[0], program.target_schema, {"cr-120-damage"})
    if not required or DEALT_LIFE_GAIN_CAPABILITY not in required:
        raise ValueError("Dealt damage life gain requires one complete fixed player batch")
    return required


def damage_death_exile_scope_is_closed(effect, target_schema) -> bool:
    try:
        scope = damage_death_exile_scope(effect)
    except ValueError:
        return False
    if effect.get('op') == 'damage_fixed_set':
        return scope is DamageDeathExileScope.DEALT_CREATURES
    if effect.get('op') != 'damage' or effect.get('target') != '$target.0':
        return False
    if scope is DamageDeathExileScope.DEALT_CREATURES:
        return True
    if scope is DamageDeathExileScope.TARGET_CREATURE_OR_PLANESWALKER:
        return target_schema == _FIXED_DAMAGE_TARGET_SCHEMAS['creature_or_planeswalker']
    if scope is not DamageDeathExileScope.TARGET:
        return False
    if target_schema == _FIXED_DAMAGE_TARGET_SCHEMAS['creature']:
        return True
    if not direct_permanent_target_schema_is_closed(target_schema):
        return False
    direct = DirectPermanentTargetSpec.from_target_schema(target_schema)
    return 'creature' in direct.types_all or direct.types_any == ('creature',)


def _announced_damage_capabilities(effect, target_schema, mechanics) -> tuple[str, ...]:
    schema = dict(target_schema or {})
    if set(schema) != {"groups", DAMAGE_ALLOCATION_FIELD}:
        return ()
    try:
        spec = validate_announced_damage_effect(effect)
        if damage_allocation_spec(schema) != spec:
            return ()
        group = dict(schema["groups"][0])
    except (TypeError, ValueError):
        return ()
    minimum, maximum = group.get("min"), group.get("max")
    if "count" in group or type(minimum) is not int or type(maximum) is not int:
        return ()
    if isinstance(spec, VariableDamageAllocationSpec):
        if minimum != 0 or maximum not in (0, 2):
            return ()
        amount = 1
        allocation_capabilities = spec.capability_dependencies
    else:
        if not 0 <= minimum <= maximum <= spec.total or maximum == 0:
            return ()
        amount = spec.total
        allocation_capabilities = (ANNOUNCED_DAMAGE_CAPABILITY,)
    del group["min"], group["max"]
    group["count"] = 1
    if (isinstance(spec, VariableDamageAllocationSpec) and spec.quantity == TURN_DAMAGE_MAXIMUM
            and (maximum != 0 or group != _FIXED_DAMAGE_TARGET_SCHEMAS["creature"])):
        return ()
    direct = {"op": "damage", "source": "$source", "target": "$target.0", "amount": amount}
    base = _fixed_direct_damage_capabilities(direct, group, mechanics)
    if not base:
        return ()
    return tuple(sorted({*base, *allocation_capabilities, "damage.batch.fixed_set", "target.revalidate_resolution"}))


def variable_damage_allocation_program_requirements(program) -> tuple[str, ...]:
    schema = program.target_schema
    if (program.event != "resolve" or not program.ability_id.startswith("spell:")
            or len(program.effects) != 1 or variable_damage_allocation(schema) is None):
        raise ValueError("Variable damage allocation requires one complete cast-time spell result")
    required = _announced_damage_capabilities(program.effects[0], schema, program.coverage)
    if not required:
        raise ValueError("Variable damage allocation requires matching effect and target totals")
    return required


def _turn_damage_prevention_capabilities(effects, target_schema):
    try:
        if len(effects) == 1 and target_schema is None:
            validate_temporary_damage_prevention_prohibition(effects[0])
            return (UNPREVENTABLE_CAPABILITY,)
        if len(effects) != 3:
            return ()
        life, prohibition, damage = effects
        if validate_temporary_life_gain_effect(life).affected_player_relation != "any":
            return ()
        validate_temporary_damage_prevention_prohibition(prohibition)
    except (TypeError, ValueError):
        return ()
    schema = thaw_value(target_schema)
    if schema not in (_FIXED_DAMAGE_TARGET_SCHEMAS["any_target"], _FIXED_DAMAGE_TARGET_SCHEMAS["player_or_planeswalker"]):
        return ()
    damage_capabilities = _fixed_direct_damage_capabilities(damage, schema, {"cr-115-targets"})
    if not damage_capabilities:
        return ()
    return tuple(sorted({
        *damage_capabilities, UNPREVENTABLE_CAPABILITY, LIFE_GAIN_PROHIBITION_CAPABILITY,
        "life.change.effect", "target.revalidate_resolution",
    }))


def turn_damage_prevention_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects
    from .prevention_component_shapes import targeted_prevention_result_runtime_dependencies

    if targeted := targeted_prevention_result_runtime_dependencies(program):
        return targeted

    handlers = tuple(getattr(program, "handlers", ()))
    effects = (*program.effects, *handlers)
    if not any(TEMPORARY_DAMAGE_PROHIBITION_FIELD in effect for effect in nested_effects(effects)):
        return ()
    if (program.event, program.active_zone) != ("resolve", "stack") or handlers:
        raise ValueError("Turn damage prevention prohibition requires a complete spell resolution")
    required = _turn_damage_prevention_capabilities(program.effects, program.target_schema)
    if not required:
        raise ValueError("Turn damage prevention prohibition requires the exact order and target domain")
    return required


def fixed_damage_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for the closed fixed-damage vocabulary."""
    # Delegating recognizers may inspect an iterable before declining it.
    # Preserve the same mechanics for every independently owned shape.
    mechanic_ids = tuple(mechanic_ids)
    if any(TEMPORARY_DAMAGE_PROHIBITION_FIELD in effect for effect in effects):
        return _turn_damage_prevention_capabilities(effects, target_schema)
    from ..turn_match_outcome_model import TURN_OUTCOME_FIELD
    from .turn_match_outcome_protection import turn_match_outcome_node_capabilities

    if any(TURN_OUTCOME_FIELD in effect for effect in effects):
        return turn_match_outcome_node_capabilities(effects, target_schema)
    from .event_damage_sources import event_damage_source_node_capabilities
    dealer = event_damage_source_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    if dealer:return dealer
    from .referenced_damage_shapes import referenced_damage_node_capabilities
    referenced=referenced_damage_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
    if referenced:return referenced

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cr-120-damage" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    from .random_activation_target import (
        RANDOM_ACTIVATION_TARGET_CAPABILITY, RANDOM_ACTIVATION_TARGET_FIELD, ordinary_random_activation_damage,
    )
    from .announced_three_target_damage import FIELD as SPELL_X_TARGET_FIELD, node_dependencies as spell_x_target_dependencies

    if SPELL_X_TARGET_FIELD in effect:
        return spell_x_target_dependencies(effect, target_schema)
    if RANDOM_ACTIVATION_TARGET_FIELD in effect:
        if target_schema != _FIXED_DAMAGE_TARGET_SCHEMAS["any_target"] or type(target_schema["count"]) is not int:
            return ()
        try:
            ordinary = ordinary_random_activation_damage(effect)
        except ValueError:
            return ()
        required = _fixed_direct_damage_capabilities(ordinary, target_schema, mechanics)
        return (*required, RANDOM_ACTIVATION_TARGET_CAPABILITY) if required else ()
    if SOURCE_RELATIVE_DAMAGE_VERSION_FIELD in effect:
        if target_schema is not None:
            return ()
        try:
            return _source_relative_damage_dependencies(source_relative_damage_role(effect))
        except (TypeError, ValueError):
            return ()
    if 'fixed_target_role_damage' in effect:
        from .fixed_damage_roles import fixed_damage_role_node_capabilities
        return fixed_damage_role_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if 'fixed_target_controller_damage' in effect:
        from .fixed_dual_damage import fixed_dual_damage_node_capabilities
        return fixed_dual_damage_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    from .announced_x_damage_sets import SCALED_ANNOUNCED_TARGET_DAMAGE_FIELD, scaled_announced_target_damage_node_capabilities

    if SCALED_ANNOUNCED_TARGET_DAMAGE_FIELD in effect:
        return scaled_announced_target_damage_node_capabilities(effects, target_schema, mechanics)
    if ANNOUNCED_DAMAGE_EFFECT_FIELD in effect:
        return _announced_damage_capabilities(effect, target_schema, mechanics)
    if DAMAGE_DEATH_EXILE_FIELD in effect:
        if DAMAGE_DEATH_EXILE_MECHANIC not in mechanics or not damage_death_exile_scope_is_closed(effect, target_schema):
            return ()
        body = {key: value for key, value in effect.items() if key != DAMAGE_DEATH_EXILE_FIELD}
        base = fixed_damage_node_capabilities(effects=(body,), target_schema=target_schema, mechanic_ids=mechanics)
        return (*base, DAMAGE_DEATH_EXILE_CAPABILITY) if base else ()
    if (target_schema is None and set(effect) == {'op', 'source', 'target', 'amount'}
            and effect.get('op') == 'damage' and effect.get('source') == '$source'
            and effect.get('target') == '$event.block_counterpart' and _positive_int(effect.get('amount'))):
        return ('damage.amount.positive', 'damage.result.multitype_permanent',
                'damage.source.stack_current_or_lki', 'trigger.event.block_pair_counterpart')
    if (target_schema is None and isinstance(effect.get('target'),Mapping)
            and effect['target'].get('kind')=='target_controller_information'):
        from ..target_controller_information import controller_reference_index,TARGET_CONTROLLER_INFORMATION_CAPABILITY,TARGET_CONTROLLER_INFORMATION_MECHANIC
        try:index=controller_reference_index(effect['target'])
        except (TypeError,ValueError):return ()
        if (TARGET_CONTROLLER_INFORMATION_MECHANIC not in mechanics or index!=0
                or set(effect)!={'op','source','target','amount'} or effect['op']!='damage'
                or effect['source']!='$source' or not _positive_int(effect['amount'])):return ()
        return (TARGET_CONTROLLER_INFORMATION_CAPABILITY,'damage.amount.positive','damage.result.player_life','damage.source.stack_current_or_lki')
    if effect.get(DEALT_LIFE_GAIN_FIELD) == TARGETED_DEALT_LIFE_GAIN_VERSION:
        return _targeted_dealt_life_gain_capabilities(effect, target_schema, mechanics)
    fixed_set = _fixed_set_damage_capabilities(
        effect, target_schema, mechanics
    )
    if fixed_set is not None:
        return fixed_set
    if (target_schema is None and set(effect)=={'op','source','target','amount'}
            and effect.get('op')=='damage' and effect.get('source')=='$source'
            and effect.get('target')=='$controller' and _positive_int(effect.get('amount'))):
        return ('damage.amount.positive','damage.result.player_life','damage.source.stack_current_or_lki')
    if (
        target_schema is None
        and set(effect) == {"op", "source", "amount"}
        and effect.get("op") == "damage_each_opponent"
        and effect.get("source") == "$source"
        and _positive_int(effect.get("amount"))
    ):
        return ("damage.amount.positive", "damage.result.player_life", "damage.source.stack_current_or_lki")
    return _fixed_direct_damage_capabilities(effect, target_schema, mechanics)


__all__ = ["fixed_damage_node_capabilities"]
