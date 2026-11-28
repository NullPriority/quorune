"""Typed temporary copies of public permanents."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from ..activation_condition_model import ActivationCondition, ActivationConditionKind
from ..entry_designations import CHOSEN_CREATURE_TYPE_TARGET_PREDICATE
from ..replacement.immutable import FrozenMap, thaw_value
from ..token_copy_recipes import TokenCopyExceptionSpec, apply_copy_exception
from ..targets import target_plan
from .graveyard_card_targets import PublicGraveyardCardTargetKind, PublicGraveyardCardTargetSpec


TEMPORARY_SELF_COPY_OPERATION = "copy_until_end_of_turn"
TEMPORARY_SELF_COPY_VERSION = "temporary_self_copy_version"
TEMPORARY_SELF_COPY_CAPABILITY = "continuous.resolution.temporary_self_copy"
TEMPORARY_ARTIFACT_COPY_CAPABILITY = "continuous.resolution.temporary_self_copy_artifact_exception"
TEMPORARY_GRAVEYARD_COPY_CAPABILITY = "continuous.resolution.temporary_owned_graveyard_self_copy"
_PUBLIC_UNION = {
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1,
    "types_any": ["artifact", "creature", "enchantment", "land"],
}
_EFFECT = {
    "op": TEMPORARY_SELF_COPY_OPERATION, TEMPORARY_SELF_COPY_VERSION: 1,
    "source": "$source.zone_object", "target": "$target.0",
}


_CONTROLLED_CREATURE = {
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1,
    "types_any": ["creature"], "controller_relation": "you",
}
_LINKED_CREATURE = {**_CONTROLLED_CREATURE, "predicate": CHOSEN_CREATURE_TYPE_TARGET_PREDICATE}
_ARTIFACT_EXCEPTION = TokenCopyExceptionSpec(add_card_types=("artifact",))
_ARTIFACT_EFFECT = {
    **_EFFECT, TEMPORARY_SELF_COPY_VERSION: 2,
    "copy_exception": _ARTIFACT_EXCEPTION.to_dict(),
}

_GRAVEYARD_EFFECT = {**_EFFECT, TEMPORARY_SELF_COPY_VERSION: 3}
_GRAVEYARD_CONDITION = ActivationCondition(ActivationConditionKind.GRAVEYARD_DISTINCT_TYPES, minimum=4)
_GRAVEYARD_PRICE = {
    "text": "{2}{G}{G}", "mana": {**dict.fromkeys(("GENERIC", *"WUBRGC"), 0), "GENERIC": 2, "G": 2},
    "complex_symbols": [], "tap_source": False, "untap_source": False,
    "discard_source": False, "sacrifice_source": False, "exile_source": False,
    "life_payment": 0, "energy_payment": 0, "loyalty_delta": None,
    "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
}


def temporary_graveyard_copy_effect():
    return dict(_GRAVEYARD_EFFECT)


def temporary_graveyard_copy_target_schema():
    return PublicGraveyardCardTargetSpec(
        PublicGraveyardCardTargetKind.PERMANENT_CARD, owner_relation="you",
    ).to_target_schema()


def is_temporary_graveyard_copy_activation(ability, cost):
    mana = cost.get("mana") if isinstance(cost, Mapping) else None
    return (
        isinstance(mana, Mapping) and all(type(amount) is int for amount in mana.values())
        and type(cost.get("life_payment")) is int and type(cost.get("energy_payment")) is int
        and all(cost.get(key) is False for key in ("tap_source", "untap_source", "discard_source", "sacrifice_source", "exile_source"))
        and thaw_value(cost) == _GRAVEYARD_PRICE and ability.cost_text == _GRAVEYARD_PRICE["text"]
        and ability.zones == ("battlefield",) and not ability.mana_ability and not ability.sorcery_speed
        and ability.activation_conditions == (_GRAVEYARD_CONDITION,) and ability.activation_limit is None
    )


def temporary_self_copy_effect():
    return dict(_EFFECT)


def linked_temporary_artifact_copy_effect():
    return thaw_value(FrozenMap(_ARTIFACT_EFFECT))


def linked_temporary_artifact_copy_schema():
    return thaw_value(FrozenMap(_LINKED_CREATURE))


def temporary_self_copy_target_schema():
    return thaw_value(FrozenMap(_PUBLIC_UNION))


def is_versioned_self_copy(effect):
    return isinstance(effect, Mapping) and effect.get("op") == TEMPORARY_SELF_COPY_OPERATION and TEMPORARY_SELF_COPY_VERSION in effect


def temporary_self_copy_scope(effects, target_schema, *, projection=False):
    if (len(effects) != 1 or not is_versioned_self_copy(effects[0])
            or type(effects[0][TEMPORARY_SELF_COPY_VERSION]) is not int
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int):
        raise ValueError("Temporary self-copy requires one exact source instruction and target")
    instruction = thaw_value(FrozenMap(effects[0]))
    target = thaw_value(FrozenMap(target_schema))
    required = _CONTROLLED_CREATURE if projection else _LINKED_CREATURE
    if not ((instruction == _EFFECT and target == _PUBLIC_UNION)
            or (instruction == _ARTIFACT_EFFECT and target == required)
            or (instruction == _GRAVEYARD_EFFECT and target == temporary_graveyard_copy_target_schema())):
        raise ValueError("Temporary self-copy requires its represented public target scope")
    group, = target_plan(target).groups
    if (group.min_targets, group.max_targets) != (1, 1):
        raise ValueError("Temporary self-copy requires one mandatory target")
    return target


def _copy_capabilities(effects):
    if effects[0][TEMPORARY_SELF_COPY_VERSION] == 2:
        return (TEMPORARY_SELF_COPY_CAPABILITY, TEMPORARY_ARTIFACT_COPY_CAPABILITY, "target.revalidate_resolution")
    if effects[0][TEMPORARY_SELF_COPY_VERSION] == 3:
        return (TEMPORARY_SELF_COPY_CAPABILITY, TEMPORARY_GRAVEYARD_COPY_CAPABILITY, "target.revalidate_resolution")
    return (TEMPORARY_SELF_COPY_CAPABILITY, "target.revalidate_resolution")


def temporary_self_copy_node_capabilities(*, effects, target_schema, mechanic_ids):
    try:
        temporary_self_copy_scope(effects, target_schema, projection=True)
    except (TypeError, ValueError, KeyError):
        return ()
    if "cr-115-targets" not in mechanic_ids:
        return ()
    return _copy_capabilities(effects)


def temporary_self_copy_runtime_dependencies(program):
    if not any(is_versioned_self_copy(effect) for effect in program.effects):
        return ()
    temporary_self_copy_scope(program.effects, program.target_schema)
    if program.event != "activate" or program.active_zone != "battlefield":
        raise ValueError("Temporary self-copy requires an activated battlefield source")
    if self_copy_model_zone(program.effects[0]) == "graveyard":
        from ..compiler.activated_costs import activated_ability_cost
        from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors

        abilities = activated_abilities_from_descriptors(program.handlers)
        if (len(program.handlers) != 1 or len(abilities) != 1 or program.event_condition is not None
                or thaw_value(abilities[0].target_schema) != thaw_value(program.target_schema)
                or activated_ability_cost(abilities[0]) != thaw_value(program.cost_schema)
                or not is_temporary_graveyard_copy_activation(abilities[0], program.cost_schema)):
            raise ValueError("Temporary graveyard copy requires its ordinary delirium activation")
    return _copy_capabilities(program.effects)


def is_closed_temporary_self_copy_program(program):
    try:
        required = temporary_self_copy_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def validate_bound_self_copy(effect):
    version = effect[TEMPORARY_SELF_COPY_VERSION]
    expected = _ARTIFACT_EFFECT if version == 2 else _EFFECT
    if (set(effect) - {"_runtime_source", "reason"} != set(expected)
            or type(version) is not int or version not in (1, 2, 3)
            or type(effect["target"]) is not str or not effect["target"]
            or effect["source"] is not None and (type(effect["source"]) is not str or not effect["source"])):
        raise ValueError("Bound temporary self-copy requires its original source and one target")
    if version == 2 and TokenCopyExceptionSpec.from_dict(effect["copy_exception"]) != _ARTIFACT_EXCEPTION:
        raise ValueError("Linked temporary copy requires exactly the artifact addition")


def self_copy_model_zone(effect):
    return "graveyard" if effect[TEMPORARY_SELF_COPY_VERSION] == 3 else "battlefield"


def apply_temporary_artifact_copy_exception(effect, values):
    if effect.get(TEMPORARY_SELF_COPY_VERSION) == 2:
        return apply_copy_exception(values, _ARTIFACT_EXCEPTION)
    return values


def is_closed_versioned_copy_program(program):
    from .linked_exile_copy import is_closed_linked_exile_copy_program
    from .announced_artifact_copy import is_closed_announced_artifact_copy_program
    from .announced_graveyard_copy import is_closed_announced_graveyard_copy_program
    from .graveyard_exile_copy import is_closed_graveyard_exile_copy_program
    from .persistent_public_copy import is_closed_controlled_artifact_copy_program, is_closed_persistent_public_copy_program

    return (is_closed_temporary_self_copy_program(program) or is_closed_linked_exile_copy_program(program)
            or is_closed_announced_artifact_copy_program(program) or is_closed_announced_graveyard_copy_program(program)
            or is_closed_graveyard_exile_copy_program(program)
            or is_closed_persistent_public_copy_program(program) or is_closed_controlled_artifact_copy_program(program))


TEMPORARY_CREATURE_COPY_SET_VERSION = "temporary_creature_copy_set_version"
TEMPORARY_CREATURE_COPY_SET_CAPABILITY = "continuous.resolution.temporary_creature_copy_set"
TEMPORARY_ARTIFACT_COPY_SET_CAPABILITY = "continuous.resolution.temporary_controlled_artifact_copy_set"
_COPY_SET_DEPENDENCIES = (TEMPORARY_CREATURE_COPY_SET_CAPABILITY, "target.revalidate_resolution")
_NONLEGENDARY_COPY = TokenCopyExceptionSpec(remove_legendary=True)


class TemporaryPermanentCopySet(StrEnum):
    ALL_OTHER_CREATURES = "all_other_creatures"
    OTHER_CONTROLLED_CREATURES_NONLEGENDARY = "other_controlled_creatures_nonlegendary"
    ALL_OTHER_CREATURES_NONLEGENDARY = "all_other_creatures_nonlegendary"
    OTHER_CONTROLLED_ARTIFACTS = "other_controlled_artifacts"

    @property
    def card_type(self):
        return "artifact" if self is self.OTHER_CONTROLLED_ARTIFACTS else "creature"

    @property
    def controlled(self):
        return self in (self.OTHER_CONTROLLED_CREATURES_NONLEGENDARY, self.OTHER_CONTROLLED_ARTIFACTS)

    @property
    def removes_legendary(self):
        return self in (self.OTHER_CONTROLLED_CREATURES_NONLEGENDARY, self.ALL_OTHER_CREATURES_NONLEGENDARY)

    @property
    def controlled_model(self):
        return self is not self.ALL_OTHER_CREATURES

    @property
    def schema_version(self):
        if self is self.OTHER_CONTROLLED_ARTIFACTS:
            return 3
        return 2 if self is self.ALL_OTHER_CREATURES_NONLEGENDARY else 1

    def to_effect(self):
        return {
            "op": TEMPORARY_SELF_COPY_OPERATION, TEMPORARY_CREATURE_COPY_SET_VERSION: self.schema_version,
            "scope": self.value, "target": "$target.0",
        }

    def target_schema(self):
        scope = {
            "zones": ["battlefield"], "categories": ["permanent"], "count": 1,
            "types_any": [self.card_type],
        }
        if self.controlled_model:
            scope["controller_relation"] = "you"
        else:
            scope["supertypes_none"] = ["legendary"]
        return scope

    def recipients(self, *, actor, excluded):
        from ..object_predicate import ObjectQuerySpec

        return ObjectQuerySpec(
            zones=("battlefield",), types_all=(self.card_type,), exclude_ref=excluded,
            controller=actor if self.controlled else None,
        )


# Keep the original Python export and serialized field for prior scopes.
TemporaryCreatureCopySet = TemporaryPermanentCopySet


def is_temporary_creature_copy_set(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == TEMPORARY_SELF_COPY_OPERATION
            and TEMPORARY_CREATURE_COPY_SET_VERSION in effect)


def decode_temporary_creature_copy_set(effect, *, resolved=False):
    fields = {"op", TEMPORARY_CREATURE_COPY_SET_VERSION, "scope", "target"}
    allowed = fields | {"reason", "_runtime_source"} if resolved else fields
    if (not is_temporary_creature_copy_set(effect) or not fields <= set(effect) <= allowed
            or type(effect[TEMPORARY_CREATURE_COPY_SET_VERSION]) is not int):
        raise ValueError("Temporary creature copy set requires the exact versioned instruction")
    scope = TemporaryPermanentCopySet(effect["scope"])
    if effect[TEMPORARY_CREATURE_COPY_SET_VERSION] != scope.schema_version:
        raise ValueError("Temporary creature copy set version must match its scope")
    target = effect["target"]
    if resolved:
        if target is not None and (type(target) is not str or not target or target.startswith("$")):
            raise ValueError("Temporary creature copy set requires a resolved public target")
    elif target != "$target.0":
        raise ValueError("Temporary creature copy set changed its target")
    return scope


def temporary_creature_copy_set_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or "cr-115-targets" not in mechanic_ids:
        return ()
    try:
        scope = decode_temporary_creature_copy_set(effects[0])
    except (TypeError, ValueError):
        return ()
    if not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int:
        return ()
    if thaw_value(target_schema) != scope.target_schema():
        return ()
    if scope is TemporaryPermanentCopySet.OTHER_CONTROLLED_ARTIFACTS:
        return (TEMPORARY_ARTIFACT_COPY_SET_CAPABILITY, "target.revalidate_resolution")
    return _COPY_SET_DEPENDENCIES


def temporary_creature_copy_set_runtime_dependencies(program):
    if not any(is_temporary_creature_copy_set(effect) for effect in program.effects):
        return ()
    required = temporary_creature_copy_set_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=("cr-115-targets",),
    )
    if not required or program.event != "resolve":
        raise ValueError("Temporary creature copy sets require their complete resolving spell and target")
    return required


def is_closed_temporary_creature_copy_set_program(program):
    try:
        required = temporary_creature_copy_set_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def prepare_temporary_creature_copy_set(host, effect, actor, scope):
    """Snapshot current qualifying recipients and one set of copiable values."""
    from ..continuous_effect_state import matching_battlefield_objects
    from ..defined_token_faces import single_face_copy_values
    from ..errors import GameRuleError

    target_ref = effect["target"]
    if target_ref is None:
        return (), {}
    try:
        target = host._resolve_object(actor, target_ref, zones={"battlefield"})
    except GameRuleError:
        return (), {}
    if target.phased_out:
        return (), {}
    effective = host._effective_card_data(target)
    card_types, _, supertypes = host._type_parts(effective["type_line"])
    if scope.card_type not in card_types or (target.controller != actor if scope.controlled_model else "legendary" in supertypes):
        return (), {}
    recipients = matching_battlefield_objects(host, scope.recipients(actor=actor, excluded=target.ref))
    if not recipients:
        return (), {}
    values = single_face_copy_values(host._copyable_characteristics(target))
    if scope.removes_legendary:
        values = apply_copy_exception(values, _NONLEGENDARY_COPY)
    return recipients, values
