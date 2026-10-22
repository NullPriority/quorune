from __future__ import annotations

"""Closed controller-bound public conditions at a resolution instruction."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from .characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantityReduction
from .continuous_conditions import (
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
)
from .replacement.immutable import FrozenMap
from .spell_mana_conditions import ManaSpentCondition


RESOLUTION_CONDITION_OPERATION = "apply_if_public_condition"
RESOLUTION_CONDITION_MECHANIC = "fixed-resolution-public-condition"
RESOLUTION_CONDITION_CAPABILITY = "resolution.effect.public_condition"
PUBLIC_SELF_REPLACEMENT_CAPABILITY='resolution.effect.fixed_public_self_replacement'
SAME_TARGET_SELF_REPLACEMENT_CAPABILITY='resolution.effect.fixed_same_target_self_replacement'
PUBLIC_SAME_TARGET_SELF_REPLACEMENT_CAPABILITY='resolution.effect.public_same_target_self_replacement'
PUBLIC_SOURCE_SELF_REPLACEMENT_CAPABILITY='resolution.effect.public_source_self_replacement'
CAST_FACT_CONDITION_CAPABILITY = 'resolution.effect.fixed_cast_fact'
CAST_FACT_CONDITION_MECHANIC = 'fixed-cast-fact-condition'
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


def public_replacement_result_is_fixed(effects, mechanic_ids):
    values=tuple({key:value for key,value in effect.items() if key!='source'}
        if effect.get('op')=='place_counters' and effect.get('source')=='$source' else effect for effect in effects)
    return cast_fact_result_is_fixed(values,mechanic_ids,allow_targeted_damage_source=True)


def public_source_replacement_result_is_fixed(effects,mechanic_ids,*,source_binding='$source'):
    if any('source' in effect and effect['source']!=source_binding for effect in effects):return False
    values=tuple({key:value for key,value in effect.items() if key!='source'}
        if effect.get('op')=='mana' and effect.get('source')==source_binding else effect for effect in effects)
    return cast_fact_result_is_fixed(values,mechanic_ids,allow_fixed_damage_source=True)


def _self_replacement_result_is_fixed(effects,mechanic_ids,version,source_binding='$source'):
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


def resolution_condition_is_closed(condition: FixedPublicStateConditionSpec) -> bool:
    """Exclude object-relative bindings; this boundary binds only the controller."""

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
    return quantity is None or not (
        (quantity.schema_version != 1 and not _card_only_graveyard_count(quantity))
        or quantity.scope is CharacteristicQuantityScope.ATTACHED_TO_SOURCE
        or quantity.scope is CharacteristicQuantityScope.SOURCE_COUNTER
        or quantity.exclude_source
        or quantity.exclude_attached_object
    )


def validate_resolution_condition_instruction(
    effect: Mapping[str, Any],
) -> tuple[FixedPublicStateConditionSpec | KickedCastCondition | ManaSpentCondition, tuple[Mapping[str, Any], ...]]:
    if not isinstance(effect, Mapping):
        raise ValueError("Resolution condition instructions require an object")
    if effect.get('schema_version') in {6,7}:
        from .spell_mana_conditions import validate_paid_mana_instruction
        return validate_paid_mana_instruction(effect)
    expected_fields = {
        "op", "player", "condition", "effects",
        "mechanic_ids", "prefix_mechanic_ids",
    }
    cast_fact = effect.get('schema_version') in {2,4,5}
    replacing=effect.get('schema_version') in {3,4,5,8,9}
    if effect.get('schema_version')==8:expected_fields|={'target_binding'}
    if effect.get('schema_version')==9:expected_fields|={'source_binding'}
    if cast_fact:
        expected_fields |= {'schema_version', 'cast_fact'}
    if replacing:expected_fields|={'schema_version','otherwise_effects','otherwise_mechanic_ids'}
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
    for name in ("mechanic_ids", "prefix_mechanic_ids", *(('otherwise_mechanic_ids',) if replacing else ())):
        values = effect[name]
        if (
            not isinstance(values, (list, tuple))
            or any(type(value) is not str or not value for value in values)
            or len(set(values)) != len(values)
            or RESOLUTION_CONDITION_MECHANIC in values
        ):
            raise ValueError("Resolution condition component mechanics are malformed")
    if not effect["mechanic_ids"] or replacing and not effect['otherwise_mechanic_ids']:
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
    if not cast_fact and not resolution_condition_is_closed(condition):
        raise ValueError("Object-relative resolution conditions are unsupported")
    nested = effect["effects"]
    if not isinstance(nested, (list, tuple)) or not 1 <= len(nested) <= 8 or any(
        not isinstance(value, Mapping) or value.get("op") == RESOLUTION_CONDITION_OPERATION
        for value in nested
    ):
        raise ValueError("Resolution conditions require a bounded nonnested effect program")
    fixed=(_self_replacement_result_is_fixed(nested,effect['mechanic_ids'],effect.get('schema_version'),effect.get('source_binding','$source'))
        if cast_fact or replacing else resolution_result_is_fixed(nested,effect['mechanic_ids']))
    if not fixed:
        raise ValueError("Derived or source-relative results require a separate binding and timing boundary")
    if replacing:
        if type(effect['schema_version']) is not int:raise ValueError('Self-replacement version must be an exact integer')
        _validate_otherwise_program(effect)
    return condition, tuple(nested)


def _validate_otherwise_program(effect):
    values=effect['otherwise_effects']
    if (not isinstance(values,(list,tuple)) or not 1<=len(values)<=8
            or any(not isinstance(child,Mapping) or child.get('op')==RESOLUTION_CONDITION_OPERATION for child in values)
            or not _self_replacement_result_is_fixed(values,effect['otherwise_mechanic_ids'],effect.get('schema_version'),effect.get('source_binding','$source'))
            or 'fixed-next-turn-upkeep-draw' in effect['otherwise_mechanic_ids']):
        raise ValueError('Self-replacement default program is malformed or unrepresented')
