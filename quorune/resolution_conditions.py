from __future__ import annotations

"""Closed controller-bound public conditions at a resolution instruction."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from .cast_origin_replacement import (
    CAST_ORIGIN_REPLACEMENT_VERSIONS, CastOriginCondition, validate_cast_origin_replacement,
    is_main_phase_cast_replacement, validate_main_phase_cast_replacement,
)
from .characteristic_fragments import (
    CharacteristicQuantityReduction, CharacteristicQuantityScope, CharacteristicQuantitySpec,
)
from .continuous_conditions import (
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
    FixedPublicStateFact,
)
from .damage_prevention_scope import public_combat_prevention_replacement_side
from .monarch_counter_doubling_condition import MONARCH_COUNTER_DOUBLING_VERSION, validate_monarch_counter_doubling
from .object_predicate import ObjectQuerySpec
from .replacement.immutable import FrozenMap
from .spell_mana_conditions import ManaSpentCondition, PAID_MANA_CONDITION_CAPABILITY, PAID_MANA_CONDITION_MECHANIC
from .declared_cost_conditions import DeclaredCastCostCondition
from .paid_sacrifice_conditions import (
    PAID_SACRIFICE_CONDITION_CAPABILITY, PAID_SACRIFICE_CONDITION_MECHANIC, PAID_SACRIFICE_CONDITION_VERSION,
    ACTIVATION_PAID_LEGENDARY_AMASS_CAPABILITY, ACTIVATION_PAID_SUBTYPE_CAPABILITY, ACTIVATION_PAID_THRULL_COUNTERS_CAPABILITY,
    ACTIVATION_PAID_SUSPECTED_DRAW_CAPABILITY,
    PaidSacrificeCharacteristicCondition, validate_paid_sacrifice_instruction,
)


RESOLUTION_CONDITION_OPERATION = "apply_if_public_condition"
RESOLUTION_CONDITION_MECHANIC = "fixed-resolution-public-condition"
RESOLUTION_CONDITION_CAPABILITY = "resolution.effect.public_condition"
RESOLUTION_ALTERNATIVE_SCHEMA_VERSION = 13
CONDITIONAL_COPY_REFERENCE_SCHEMA_VERSION = 15
GREATEST_POWER_TARGET_DISCARD_VERSION = 19
MONARCH_KNIGHT_ENTRY_DAMAGE_VERSION = 33
MONARCH_KNIGHT_DAMAGE_MECHANICS = ("declared-effect-amount", "cr-120-damage", "public-query-effect-amount")
MONARCH_KNIGHT_PREFIX_MECHANICS = ("cr-111-tokens",)
PUBLIC_SELF_REPLACEMENT_CAPABILITY='resolution.effect.fixed_public_self_replacement'
SAME_TARGET_SELF_REPLACEMENT_CAPABILITY='resolution.effect.fixed_same_target_self_replacement'
PUBLIC_SAME_TARGET_SELF_REPLACEMENT_CAPABILITY='resolution.effect.public_same_target_self_replacement'
PUBLIC_SOURCE_SELF_REPLACEMENT_CAPABILITY='resolution.effect.public_source_self_replacement'
CAST_FACT_CONDITION_CAPABILITY = 'resolution.effect.fixed_cast_fact'
CAST_FACT_CONDITION_MECHANIC = 'fixed-cast-fact-condition'
CAST_FACT_CONDITION_MECHANICS = {
    CAST_FACT_CONDITION_CAPABILITY: CAST_FACT_CONDITION_MECHANIC,
    PAID_MANA_CONDITION_CAPABILITY: PAID_MANA_CONDITION_MECHANIC,
    PAID_SACRIFICE_CONDITION_CAPABILITY: PAID_SACRIFICE_CONDITION_MECHANIC,
    ACTIVATION_PAID_SUBTYPE_CAPABILITY: PAID_SACRIFICE_CONDITION_MECHANIC,
    ACTIVATION_PAID_LEGENDARY_AMASS_CAPABILITY: PAID_SACRIFICE_CONDITION_MECHANIC,
    ACTIVATION_PAID_THRULL_COUNTERS_CAPABILITY: PAID_SACRIFICE_CONDITION_MECHANIC,
    ACTIVATION_PAID_SUSPECTED_DRAW_CAPABILITY: PAID_SACRIFICE_CONDITION_MECHANIC,
}
_DERIVED_RESULT_MECHANICS = frozenset({
    "declared-effect-amount", "public-query-effect-amount", "scalar-effect-amount",
})
_DERIVED_VALUE_KINDS = frozenset({
    "declared_effect_amount", "public_query_effect_amount", "scalar_effect_amount",
})


def _contains_derived_result_value(value: Any) -> bool:
    if isinstance(value, str):
        return value == "$source" or value.startswith("$source.")
    if isinstance(value, Mapping):
        return value.get("op") == "delayed_trigger" or value.get("kind") in _DERIVED_VALUE_KINDS or any(
            _contains_derived_result_value(child) for child in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_derived_result_value(child) for child in value)
    return False


def resolution_result_is_fixed(effects: Any, mechanic_ids: Any) -> bool:
    """Keep unproved quantities and source bindings outside this fixed slice."""

    return not _DERIVED_RESULT_MECHANICS.intersection(mechanic_ids) and not _contains_derived_result_value(effects)


def cast_fact_result_is_fixed(effects: Any, mechanic_ids: Any, *, allow_fixed_damage_source: bool=False,
        allow_targeted_damage_source: bool=False) -> bool:
    """An adopted instruction's source attribution is not a result quantity."""
    attributed={'place_counters_on_set','place_counter_set'}
    if allow_fixed_damage_source:
        attributed.add('damage_fixed_set')
    if allow_targeted_damage_source:
        attributed.add('damage')
    values = tuple(
        {key: value for key, value in effect.items() if key != 'source'}
        if effect.get('op') in attributed and effect.get('source') == '$source'
        else effect for effect in effects
    )
    return resolution_result_is_fixed(values, mechanic_ids)


_ANNOUNCED_DAMAGE_MECHANICS = frozenset(("declared-effect-amount", "cr-120-damage", "cr-115-targets"))


def _announced_replacement_damage_amount(effects, mechanics):
    from .query_effect_amount_model import CastXAmountSpec

    if len(effects) != 1 or set(mechanics) != _ANNOUNCED_DAMAGE_MECHANICS:
        return None
    effect = effects[0]
    amount = effect.get("amount")
    fields = {"op", "source", "target", "amount"}
    if type(amount) is int:
        fields.add("source_snapshot")
    if (
        set(effect) != fields
        or effect["op"] != "damage"
        or any(type(effect[key]) is not str or not effect[key] for key in ("source", "target"))
    ):
        return None
    if type(amount) is int:
        from .damage_source import DamageSourceSnapshot

        try:
            snapshot = DamageSourceSnapshot.from_dict(effect["source_snapshot"])
        except (TypeError, ValueError):
            return None
        if effect["source"].startswith("$") or effect["target"].startswith("$"):
            return None
        return amount if amount >= 0 and snapshot.ref == effect["source"] else None
    try:
        spec = CastXAmountSpec.from_dict(amount)
    except (TypeError, ValueError):
        return None
    return spec if spec.coefficient == 1 and spec.schema_version in {1, 3} else None


def public_replacement_result_is_fixed(effects, mechanic_ids):
    if _announced_replacement_damage_amount(effects, mechanic_ids) is not None:
        return True
    values=tuple({key:value for key,value in effect.items() if key!='source'}
        if effect.get('op')=='place_counters' and effect.get('source')=='$source' else effect for effect in effects)
    return cast_fact_result_is_fixed(values,mechanic_ids,allow_targeted_damage_source=True)


def public_source_replacement_result_is_fixed(effects,mechanic_ids,*,source_binding='$source'):
    if any('source' in effect and effect['source']!=source_binding for effect in effects):return False

    values = tuple(
        {key: value for key, value in effect.items() if key != "source"}
        if effect.get("source") == source_binding and (
            effect.get("op") == "mana"
            or effect.get("op") == "create_damage_prevention_shield"
            and public_combat_prevention_replacement_side(effect, source_binding=source_binding) is not None
        ) else effect
        for effect in effects
    )
    return cast_fact_result_is_fixed(values,mechanic_ids,allow_fixed_damage_source=True)


def _controller_power_minimum_is_closed(condition):
    from .object_predicate import ObjectQuerySpec

    predicate = condition.predicate
    numeric = predicate.numeric_characteristic if predicate is not None else None
    return (
        condition.kind is FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS
        and numeric is not None and numeric.characteristic.value == "power" and numeric.comparison.value == "at_least"
        and predicate == ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), numeric_characteristic=numeric)
    )


def _validate_public_announced_damage_replacement(effect, condition):
    from .query_effect_amount_model import CastXAmountSpec

    normal = _announced_replacement_damage_amount(effect["otherwise_effects"], effect["otherwise_mechanic_ids"])
    replacement = _announced_replacement_damage_amount(effect["effects"], effect["mechanic_ids"])
    schema = effect["target_binding"]
    valid_bonus = (
        normal == CastXAmountSpec() and replacement == CastXAmountSpec(schema_version=3, bonus=2)
        or type(normal) is int and type(replacement) is int and replacement == normal + 2
    )
    if (
        not valid_bonus or effect["prefix_mechanic_ids"] or not _controller_power_minimum_is_closed(condition)
        or set(schema) != {"zones", "categories", "predicate", "count"}
        or len(schema["zones"]) != 2 or set(schema["zones"]) != {"player", "battlefield"}
        or len(schema["categories"]) != 2 or set(schema["categories"]) != {"player", "permanent"}
        or schema["predicate"] != "damageable"
        or any(effect["effects"][0].get(key) != effect["otherwise_effects"][0].get(key)
               for key in ("source", "target", "source_snapshot"))
    ):
        raise ValueError("Announced damage self-replacement requires the same target, source, and exact cast-X bonus")


def _validate_public_combat_prevention_replacement(effect, condition):
    normal = effect["otherwise_effects"]
    replacement = effect["effects"]
    binding = effect["source_binding"]
    if (
        effect["prefix_mechanic_ids"]
        or not _controller_power_minimum_is_closed(condition)
        or len(normal) != 1 or len(replacement) != 1
        or public_combat_prevention_replacement_side(normal[0], source_binding=binding) != "all"
        or public_combat_prevention_replacement_side(replacement[0], source_binding=binding) != "opponent_creatures"
    ):
        raise ValueError("Combat prevention self-replacement requires the exact two shield scopes and a fixed power minimum")


def _conditional_branch_result_is_fixed(effects,mechanic_ids,version,source_binding='$source'):
    if version == RESOLUTION_ALTERNATIVE_SCHEMA_VERSION:
        return len(effects) == 1 and resolution_result_is_fixed(effects, mechanic_ids)
    if version==8:return public_replacement_result_is_fixed(effects,mechanic_ids)
    if version==9:return public_source_replacement_result_is_fixed(effects,mechanic_ids,source_binding=source_binding)
    return cast_fact_result_is_fixed(effects,mechanic_ids,
        allow_fixed_damage_source=version in {4,5},allow_targeted_damage_source=version==5)


@dataclass(frozen=True, slots=True)
class ResolutionConditionBinding:
    """The resolving controller, independent of a source's current incarnation."""

    controller: str
    object_id: str = "resolution-controller"
    counters: FrozenMap = field(default_factory=FrozenMap)
    entered_battlefield_turn_sequence: int = 0


@dataclass(frozen=True, slots=True)
class KickedCastCondition:
    kind: str = 'kicked_cast'
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': self.schema_version, 'kind': self.kind}


def _card_only_graveyard_count(quantity) -> bool:
    """Admit the versioned card filter without admitting new arithmetic."""
    return (quantity.schema_version == 2
        and quantity.scope in {CharacteristicQuantityScope.CONTROLLER_ZONE,
            CharacteristicQuantityScope.OPPONENT_ZONES, CharacteristicQuantityScope.ALL_ZONES}
        and quantity.query is not None and quantity.query.zones == ('graveyard',)
        and quantity.query.token is False
        and quantity.reduction is CharacteristicQuantityReduction.OBJECT_COUNT
        and quantity.multiplier == 1 and quantity.offset == 0
        and not quantity.terms and not quantity.mana_colors)


CONTROLLER_BASIC_LAND_TYPES = CharacteristicQuantitySpec(
    scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
    query=ObjectQuerySpec(zones=("battlefield",), types_all=("land",)),
    schema_version=2,
    reduction=CharacteristicQuantityReduction.DISTINCT_BASIC_LAND_TYPES,
)
THREE_BASIC_LAND_TYPES_CONDITION = FixedPublicStateConditionSpec(
    FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST,
    amount=3,
    quantity=CONTROLLER_BASIC_LAND_TYPES,
    schema_version=2,
)
FIVE_BASIC_LAND_TYPES_CONDITION = FixedPublicStateConditionSpec(
    FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST,
    amount=5,
    quantity=CONTROLLER_BASIC_LAND_TYPES,
    schema_version=2,
)


def resolution_condition_is_closed(
    condition: FixedPublicStateConditionSpec, *, allow_keyword_count: bool = False,
) -> bool:
    """Bind the condition to the resolving controller."""

    if condition in (THREE_BASIC_LAND_TYPES_CONDITION, FIVE_BASIC_LAND_TYPES_CONDITION):
        return True
    if condition.kind in {
        FixedPublicStateConditionKind.SOURCE_ENTERED_THIS_TURN,
        FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST,
        FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
        FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY,
    }:
        return False
    if condition.fact is not None and (
        condition.fact.value.startswith("source_")
        or condition.fact.value == "controller_other_creatures_entered_this_turn"
    ):
        return False
    quantity = condition.quantity
    keyword_count = (
        allow_keyword_count and quantity is not None and quantity.schema_version == 6
        and quantity.scope is CharacteristicQuantityScope.CONTROLLER_ZONE
        and condition.kind is FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST
        and condition.amount == 1 and quantity.query is not None and bool(quantity.query.keywords_all)
        and not quantity.query.keywords_none
    )
    return quantity is None or not (
        (quantity.schema_version != 1 and not _card_only_graveyard_count(quantity) and not keyword_count)
        or quantity.scope is CharacteristicQuantityScope.ATTACHED_TO_SOURCE
        or quantity.scope is CharacteristicQuantityScope.SOURCE_COUNTER
        or quantity.exclude_source
        or quantity.exclude_attached_object
    )


def _validate_greatest_power_target_discard(effect, condition, children):
    if (
        type(effect["schema_version"]) is not int
        or condition.fact is not FixedPublicStateFact.CONTROLS_A_GREATEST_POWER_CREATURE
        or effect["prefix_mechanic_ids"] or len(children) != 1
    ):
        raise ValueError("Conditional opponent discard requires the exact current greatest-power result")
    child = children[0]
    actor, target = effect["player"], child.get("target")
    if type(target) is not str or not target:
        raise ValueError("Conditional opponent discard requires its bound target")
    if actor == "$controller":
        if target != "$target.0":
            raise ValueError("Conditional opponent discard requires the announced target slot")
    elif actor.startswith("$") or target.startswith("$") or actor == target:
        raise ValueError("Conditional opponent discard requires another bound seat")
    if (
        child.get("op") != "choose_cards_apnap" or child.get("actor") != actor
        or child.get("zone") != "hand" or child.get("then") != "discard"
        or type(child.get("count")) is not int or child["count"] != 1
        or child.get("hidden") is not True or not isinstance(child.get("players"), (list, tuple))
        or tuple(child["players"]) != (target,)
    ):
        raise ValueError("Conditional opponent discard requires one private hand choice by the target")


def _validate_monarch_knight_damage(effect):
    from .query_effect_amount_model import PublicQueryAmountSpec

    monarch = FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_IS_MONARCH)
    opponents = FrozenMap({"groups": [{"kind": "players", "relation": "opponents"}]})
    creatures = CharacteristicQuantitySpec(
        scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
        query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
    )
    if (type(effect.get("schema_version")) is not int or effect.get("op") != RESOLUTION_CONDITION_OPERATION
            or set(effect) != {"op", "schema_version", "player", "condition", "effects", "mechanic_ids", "prefix_mechanic_ids"}
            or type(effect["player"]) is not str or not effect["player"]
            or not isinstance(effect["condition"], Mapping) or FrozenMap(effect["condition"]) != FrozenMap(monarch.to_dict())
            or tuple(effect["mechanic_ids"]) != MONARCH_KNIGHT_DAMAGE_MECHANICS
            or tuple(effect["prefix_mechanic_ids"]) != MONARCH_KNIGHT_PREFIX_MECHANICS
            or not isinstance(effect["effects"], (list, tuple)) or len(effect["effects"]) != 1):
        raise ValueError("Monarch damage requires its exact resolving controller and ordered prefix")
    damage, = effect["effects"]
    symbolic = effect["player"] == "$controller"
    damage_fields = {"op", "source", "amount", "groups"}
    if not symbolic:
        damage_fields.add("source_snapshot")
    if (not isinstance(damage, Mapping) or set(damage) != damage_fields
            or damage["op"] != "damage_fixed_set" or FrozenMap({"groups": damage["groups"]}) != opponents):
        raise ValueError("Monarch damage requires the original source and a single opponent batch")
    source, amount = damage["source"], damage["amount"]
    if symbolic:
        spec = PublicQueryAmountSpec.from_dict(amount)
        if source != "$source" or spec.schema_version != 2 or spec.coefficient != 1 or spec.quantity != creatures:
            raise ValueError("Monarch damage needs the declared live creature count")
    else:
        from .damage_source import DamageSourceSnapshot

        if (effect["player"].startswith("$") or type(source) is not str or not source or source.startswith("$")
                or type(amount) is not int or amount < 0
                or DamageSourceSnapshot.from_dict(damage["source_snapshot"]).ref != source):
            raise ValueError("The original source and live creature count must be bound at resolution")
    return monarch, (damage,)



def validate_resolution_condition_instruction(
    effect: Mapping[str, Any],
) -> tuple[FixedPublicStateConditionSpec | KickedCastCondition | ManaSpentCondition | DeclaredCastCostCondition | PaidSacrificeCharacteristicCondition | CastOriginCondition, tuple[Mapping[str, Any], ...]]:
    if not isinstance(effect, Mapping):
        raise ValueError("Resolution condition instructions require an object")
    if effect.get("schema_version") in {MONARCH_KNIGHT_ENTRY_DAMAGE_VERSION, MONARCH_COUNTER_DOUBLING_VERSION}:
        validator = _validate_monarch_knight_damage if effect["schema_version"] == MONARCH_KNIGHT_ENTRY_DAMAGE_VERSION else validate_monarch_counter_doubling
        return validator(effect)
    if is_main_phase_cast_replacement(effect):
        condition, alternate, _target = validate_main_phase_cast_replacement(effect, bound=type(effect.get("cast_fact")) is bool)
        return condition, alternate
    if effect.get("schema_version") in CAST_ORIGIN_REPLACEMENT_VERSIONS:
        return validate_cast_origin_replacement(effect, bound=type(effect.get("cast_fact")) is bool)
    if effect.get('schema_version') == 18:
        from .paid_alternative_cast_model import validate_paid_alternative_instruction
        return validate_paid_alternative_instruction(effect,allow_bound=True)
    if effect.get('schema_version') == 17:
        from .target_replacement_model import validate_current_target_replacement
        return validate_current_target_replacement(effect)
    if effect.get('schema_version') == 16:
        from .conditional_target_preparation_model import validate_preparation_instruction
        return validate_preparation_instruction(effect)
    if effect.get("schema_version") == PAID_SACRIFICE_CONDITION_VERSION:
        return validate_paid_sacrifice_instruction(effect)
    if effect.get('schema_version') == 12:
        from .declared_cost_conditions import validate_declared_cost_instruction
        return validate_declared_cost_instruction(effect)
    if effect.get('schema_version')==11:
        from .kicked_target_replacement_model import validate_kicked_target_replacement_instruction
        return validate_kicked_target_replacement_instruction(effect)
    if effect.get('schema_version')==10:
        from .kicked_target_model import validate_kicked_target_instruction
        return validate_kicked_target_instruction(effect)
    if effect.get('schema_version') in {6,7}:
        from .spell_mana_conditions import validate_paid_mana_instruction
        return validate_paid_mana_instruction(effect)
    expected_fields = {
        "op", "player", "condition", "effects",
        "mechanic_ids", "prefix_mechanic_ids",
    }
    greatest_power_discard = effect.get("schema_version") == GREATEST_POWER_TARGET_DISCARD_VERSION
    if greatest_power_discard:
        expected_fields.add("schema_version")
    cast_fact = effect.get('schema_version') in {2,4,5}
    ordinary_alternative = effect.get('schema_version') == RESOLUTION_ALTERNATIVE_SCHEMA_VERSION
    has_otherwise = effect.get('schema_version') in {3, 4, 5, 8, 9, RESOLUTION_ALTERNATIVE_SCHEMA_VERSION, CONDITIONAL_COPY_REFERENCE_SCHEMA_VERSION}
    if effect.get('schema_version')==8:expected_fields|={'target_binding'}
    if effect.get('schema_version')==9:expected_fields|={'source_binding'}
    if cast_fact:
        expected_fields |= {'schema_version', 'cast_fact'}
    if has_otherwise:expected_fields|={'schema_version','otherwise_effects','otherwise_mechanic_ids'}
    if (
        not isinstance(effect, Mapping)
        or set(effect) != expected_fields
        or effect.get("op") != RESOLUTION_CONDITION_OPERATION
    ):
        raise ValueError("Resolution condition instructions have a closed schema")
    if effect.get('schema_version')==8:
        from .public_replacement_model import public_target_binding_closed
        if not public_target_binding_closed(effect['target_binding']):
            raise ValueError('Public self-replacement requires one complete unchanged public target binding')
    if effect.get('schema_version')==9:
        binding=effect['source_binding']
        if type(binding) is not str or not binding or binding.startswith('$') and binding!='$source':
            raise ValueError('Public source result requires its current bound spell source')
    if type(effect.get("player")) is not str or not effect["player"]:
        raise ValueError("Resolution conditions require a controller binding")
    for name in ("mechanic_ids", "prefix_mechanic_ids", *(('otherwise_mechanic_ids',) if has_otherwise else ())):
        values = effect[name]
        if (
            not isinstance(values, (list, tuple))
            or any(type(value) is not str or not value for value in values)
            or len(set(values)) != len(values)
            or RESOLUTION_CONDITION_MECHANIC in values
        ):
            raise ValueError("Resolution condition component mechanics are malformed")
    if not effect["mechanic_ids"] or has_otherwise and not effect['otherwise_mechanic_ids']:
        raise ValueError("Resolution condition result mechanics must be present")
    if "fixed-next-turn-upkeep-draw" in (*effect["mechanic_ids"], *effect["prefix_mechanic_ids"]):
        raise ValueError("Delayed draw remains outside conditional programs")
    if cast_fact:
        if type(effect['schema_version']) is not int or effect['condition'] != KickedCastCondition().to_dict() or not (
            type(effect['cast_fact']) is bool or effect['cast_fact'] == '$context.kicked'
        ):
            raise ValueError('Cast fact conditions require one sealed kicked marker')
        condition = KickedCastCondition()
    else:
        condition = FixedPublicStateConditionSpec.from_dict(effect["condition"])
    if not cast_fact and not resolution_condition_is_closed(
        condition, allow_keyword_count=effect.get('schema_version') == 8,
    ):
        raise ValueError("Object-relative resolution conditions are unsupported")
    if ordinary_alternative and not all(resolution_condition_is_closed(leaf) for leaf in condition.leaf_conditions()):
        raise ValueError("Each branch condition leaf must bind only the resolving controller")
    nested = effect["effects"]
    if not isinstance(nested, (list, tuple)) or not 1 <= len(nested) <= 8 or any(
        not isinstance(value, Mapping) or value.get("op") == RESOLUTION_CONDITION_OPERATION
        for value in nested
    ):
        raise ValueError("Resolution conditions require a bounded nonnested effect program")
    fixed=(_conditional_branch_result_is_fixed(nested,effect['mechanic_ids'],effect.get('schema_version'),effect.get('source_binding','$source'))
        if cast_fact or has_otherwise else resolution_result_is_fixed(nested,effect['mechanic_ids']))
    if not fixed:
        raise ValueError("Derived or source-relative results require a separate binding and timing boundary")
    if has_otherwise:
        if type(effect['schema_version']) is not int:raise ValueError('Conditional branch version must be an exact integer')
        _validate_otherwise_program(effect)
        if effect["schema_version"] == 8 and (
            "declared-effect-amount" in effect["mechanic_ids"] or "declared-effect-amount" in effect["otherwise_mechanic_ids"]
        ):
            _validate_public_announced_damage_replacement(effect, condition)
        if effect["schema_version"] == 9 and any(
            value.get("op") == "create_damage_prevention_shield" for value in (*nested, *effect["otherwise_effects"])
        ):
            _validate_public_combat_prevention_replacement(effect, condition)
    if greatest_power_discard:
        _validate_greatest_power_target_discard(effect, condition, nested)
    return condition, tuple(nested)


def _validate_otherwise_program(effect):
    values=effect['otherwise_effects']
    if (not isinstance(values,(list,tuple)) or not 1<=len(values)<=8
            or any(not isinstance(child,Mapping) or child.get('op')==RESOLUTION_CONDITION_OPERATION for child in values)
            or not _conditional_branch_result_is_fixed(values,effect['otherwise_mechanic_ids'],effect.get('schema_version'),effect.get('source_binding','$source'))
            or 'fixed-next-turn-upkeep-draw' in effect['otherwise_mechanic_ids']):
        raise ValueError('Self-replacement default program is malformed or unrepresented')
