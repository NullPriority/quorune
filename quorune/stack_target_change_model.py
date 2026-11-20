from __future__ import annotations

"""Fixed target changes for a single selected spell or ability."""

from collections.abc import Mapping
from dataclasses import dataclass

from .replacement.immutable import thaw_value


SINGLE_ANNOUNCED_STACK_TARGET = "single_announced_stack_target"
MANDATORY_STACK_TARGET_CHANGE_FIELD = "mandatory_single_stack_target_change"
OPTIONAL_STACK_TARGET_CHANGE_FIELD = "optional_stack_target_change"
STACK_SPELL_CONTROL_FIELD = "stack_spell_control"
STACK_SPELL_CONTROL_CONTEXT = "stack_spell_control_history"
STACK_TARGET_CHANGE_PAYMENT_VERSION = 15
STACK_TARGET_CHANGE_FIELDS = frozenset((MANDATORY_STACK_TARGET_CHANGE_FIELD, OPTIONAL_STACK_TARGET_CHANGE_FIELD))
REPRESENTED_STACK_TARGET_CHANGE_KINDS = frozenset(("spell", "spell_copy", "activated_ability", "triggered_ability"))


@dataclass(frozen=True, slots=True)
class SingleStackTargetChangeSpec:
    spell_or_ability: bool

    def __post_init__(self):
        if type(self.spell_or_ability) is not bool:
            raise ValueError("Single stack target change requires an exact stack scope")

    @property
    def effects(self):
        return ({"op": "retarget_stack_item", "stack": "$target.0", MANDATORY_STACK_TARGET_CHANGE_FIELD: 1},)

    @property
    def target_schema(self):
        return {
            "zones": ["stack"],
            "categories": ["spell", "ability"] if self.spell_or_ability else ["spell"],
            "predicate": SINGLE_ANNOUNCED_STACK_TARGET,
            "count": 1,
        }


@dataclass(frozen=True, slots=True)
class OptionalStackTargetChangeSpec:
    spell_or_ability: bool

    def __post_init__(self):
        if type(self.spell_or_ability) is not bool:
            raise ValueError("Optional stack target change requires an exact stack scope")

    @property
    def effects(self):
        return ({"op": "retarget_stack_item", "stack": "$target.0", OPTIONAL_STACK_TARGET_CHANGE_FIELD: 1},)

    @property
    def target_schema(self):
        return {
            "zones": ["stack"],
            "categories": ["spell", "ability"] if self.spell_or_ability else ["spell"],
            "source_exclusion": True,
            "count": 1,
        }


@dataclass(frozen=True, slots=True)
class StackSpellControlSpec:
    noncreature_only: bool

    def __post_init__(self):
        if type(self.noncreature_only) is not bool:
            raise ValueError("Spell control requires one exact spell scope")

    @property
    def effects(self):
        acquire = {
            "op": "gain_control", "stack": "$target.0",
            "controller": "$controller", STACK_SPELL_CONTROL_FIELD: 1,
        }
        return acquire, *OptionalStackTargetChangeSpec(False).effects

    @property
    def target_schema(self):
        schema = OptionalStackTargetChangeSpec(False).target_schema
        if self.noncreature_only:
            schema["types_none"] = ["creature"]
        return schema


def validate_stack_spell_control_effect(effect, *, resolved=False):
    fields = {"op", "stack", "controller", STACK_SPELL_CONTROL_FIELD}
    if resolved and "reason" in effect:
        fields.add("reason")
    marker = effect.get(STACK_SPELL_CONTROL_FIELD)
    if (set(effect) != fields or effect.get("op") != "gain_control"
            or type(marker) is not int or marker != 1):
        raise ValueError("Spell control requires the closed stack effect")
    stack = effect["stack"]
    controller = effect["controller"]
    if resolved:
        if any(type(value) is not str or not value for value in (stack, controller)):
            raise ValueError("Spell control requires its resolved stack and controller")
    elif (stack, controller) != ("$target.0", "$controller"):
        raise ValueError("Spell control requires the selected spell and resolving controller")


@dataclass(frozen=True, slots=True)
class StackSpellControlHistory:
    default_controller: str
    acquired_controllers: tuple[str, ...] = ()

    def __post_init__(self):
        if (
            type(self.default_controller) is not str or not self.default_controller
            or type(self.acquired_controllers) is not tuple
            or any(type(seat) is not str or not seat for seat in self.acquired_controllers)
            or len(set(self.acquired_controllers)) != len(self.acquired_controllers)
        ):
            raise ValueError("Malformed stack spell control history")

    @property
    def controller(self):
        return self.acquired_controllers[-1] if self.acquired_controllers else self.default_controller

    def acquire(self, actor):
        earlier = tuple(seat for seat in self.acquired_controllers if seat != actor)
        return type(self)(self.default_controller, (*earlier, actor))

    def without(self, departing):
        return type(self)(self.default_controller, tuple(seat for seat in self.acquired_controllers if seat not in departing))

    def to_dict(self):
        return {"schema_version": 1, "default_controller": self.default_controller,
                "acquired_controllers": list(self.acquired_controllers)}

    @classmethod
    def from_dict(cls, value):
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "default_controller", "acquired_controllers"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["acquired_controllers"], list)
        ):
            raise ValueError("Malformed versioned stack spell control history")
        return cls(value["default_controller"], tuple(value["acquired_controllers"]))


def stack_target_change_payment_effect():
    change, = SingleStackTargetChangeSpec(False).effects
    return {
        "op": "offer_optional_mana_payment", "schema_version": STACK_TARGET_CHANGE_PAYMENT_VERSION,
        "player": "$target.current_controller.0", "stack": "$target.0",
        "cost": {"GENERIC": 2, "W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0},
        "declined_effects": [change],
    }


def is_stack_target_change_payment(effect):
    return (
        effect.get("op") == "offer_optional_mana_payment"
        and type(effect.get("schema_version")) is int
        and effect["schema_version"] == STACK_TARGET_CHANGE_PAYMENT_VERSION
    )


def validate_stack_target_change_payment_effect(effect, *, resolved=False):
    expected = stack_target_change_payment_effect()
    value = thaw_value(effect)
    cost = value.get("cost")
    declined = value.get("declined_effects")
    if (
        not is_stack_target_change_payment(value)
        or not isinstance(cost, Mapping) or any(type(amount) is not int for amount in cost.values())
        or not isinstance(declined, list) or len(declined) != 1
        or not isinstance(declined[0], Mapping)
        or not is_mandatory_single_stack_target_change(declined[0])
    ):
        raise ValueError("Spell target change requires its exact fixed controller payment")
    if resolved:
        stack = value.get("stack")
        player = value.get("player")
        if any(type(field) is not str or not field for field in (stack, player)):
            raise ValueError("Spell target change payment requires its current spell and controller")
        expected.update(player=player, stack=stack)
        expected["declined_effects"][0]["stack"] = stack
    if value != expected:
        raise ValueError("Spell target change requires its original payer, fixed cost and declined instruction")


def matches_single_announced_stack_target(item):
    return item.kind in REPRESENTED_STACK_TARGET_CHANGE_KINDS and len(item.targets) == 1


def _has_exact_target_change_marker(effect, field):
    if field not in effect:
        return False
    marker = effect[field]
    if (
        effect.get("op") != "retarget_stack_item" or type(marker) is not int or marker != 1
        or len(STACK_TARGET_CHANGE_FIELDS.intersection(effect)) != 1
    ):
        raise ValueError("Stack target change version or selection policy is invalid")
    return True


def is_mandatory_single_stack_target_change(effect):
    return _has_exact_target_change_marker(effect, MANDATORY_STACK_TARGET_CHANGE_FIELD)


def is_optional_stack_target_change(effect):
    return _has_exact_target_change_marker(effect, OPTIONAL_STACK_TARGET_CHANGE_FIELD)
