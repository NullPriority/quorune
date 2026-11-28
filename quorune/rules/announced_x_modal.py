"""Closed choose-one modal shape sharing one announced X across its branches."""

from collections.abc import Mapping

from ..query_effect_amount_model import CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY
from ..replacement.immutable import FrozenMap, thaw_value
from ..target_cardinality import (
    ANNOUNCED_TARGET_SET_CAPABILITY, AnnouncedTargetCardinality, bind_announced_target_schema,
)

ANNOUNCED_X_MODAL_MECHANIC = "announced-x-destroy-or-life-modal"
ANNOUNCED_X_MODAL_CAPABILITY = "choice.modal.announced_x_destroy_or_life"
DESTROY_MODE = "mode_1"
LIFE_MODE = "mode_2"
DEPENDENCIES = (
    ANNOUNCED_X_MODAL_CAPABILITY, "choice.modal.fixed_one", ANNOUNCED_TARGET_SET_CAPABILITY,
    "resolution.effect.fixed_homogeneous_target_set", "permanent.destroy.effect",
    "target.permanent.characteristic_predicate", "target.revalidate_resolution",
    DECLARED_AMOUNT_CAPABILITY, "life.change.effect",
)


def _destroy_mode():
    return {
        "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["artifact", "enchantment"],
        "count_parameter": AnnouncedTargetCardinality().to_dict(),
        "effects": [{
            "op": "destroy_targets", "cards": "$targets",
            "maximum_targets": CastXAmountSpec().to_dict(), "announced_target_count": True,
        }],
        "mechanics": ["destroy", "cr-115-targets", "mana-announced-target-set", ANNOUNCED_X_MODAL_MECHANIC],
    }


def _life_mode():
    return {
        "zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1,
        "effects": [{"op": "life", "player": "$target.0", "delta": CastXAmountSpec(coefficient=2, schema_version=2).to_dict()}],
        "mechanics": ["declared-effect-amount", "cr-119-life", "cr-115-targets"],
    }


def uses_announced_x_modal(schema):
    modes = schema.get("modes") if isinstance(schema, Mapping) else None
    return isinstance(modes, Mapping) and any(
        isinstance(mode, Mapping) and isinstance(mode.get("mechanics"), (list, tuple))
        and ANNOUNCED_X_MODAL_MECHANIC in mode["mechanics"] for mode in modes.values()
    )


def validate_announced_x_modal_schema(schema):
    modes = schema.get("modes") if isinstance(schema, Mapping) else None
    if (not isinstance(modes, Mapping) or tuple(modes) != (DESTROY_MODE, LIFE_MODE)
            or type(schema.get("mode_count")) is not int
            or FrozenMap(schema) != FrozenMap({"mode_count": 1, "modes": {DESTROY_MODE: _destroy_mode(), LIFE_MODE: _life_mode()}})):
        raise ValueError("An announced-X modal spell requires its exact destruction and single life branches")
    destroy = modes[DESTROY_MODE]
    life = modes[LIFE_MODE]
    AnnouncedTargetCardinality.from_dict(destroy["count_parameter"])
    CastXAmountSpec.from_dict(destroy["effects"][0]["maximum_targets"])
    CastXAmountSpec.from_dict(life["effects"][0]["delta"])
    if type(life["count"]) is not int or destroy["effects"][0]["announced_target_count"] is not True:
        raise ValueError("The announced-X branch cardinalities must remain strictly typed")


def announced_x_modal_runtime_dependencies(program):
    if not uses_announced_x_modal(getattr(program, "target_schema", None)):
        return ()
    validate_announced_x_modal_schema(program.target_schema)
    if ((program.event, program.active_zone) != ("resolve", "stack") or program.cost_schema is not None
            or program.effects or program.handlers or not set(DEPENDENCIES).issubset(program.capability_dependencies)):
        raise ValueError("An announced-X mode requires its original spell and full direct closure")
    return DEPENDENCIES


def bind_announced_x_modal_targets(schema, declared_x):
    validate_announced_x_modal_schema(schema)
    bound = thaw_value(FrozenMap(schema))
    bound["modes"][DESTROY_MODE] = bind_announced_target_schema(bound["modes"][DESTROY_MODE], declared_x)
    return bound


def bind_announced_x_modal_stack_schema(program, item, schema):
    if not announced_x_modal_runtime_dependencies(program):
        return schema
    x = 0 if item.x_value is None else item.x_value
    if item.kind not in {"spell", "spell_copy"} or type(x) is not int or x < 0:
        raise ValueError("The original modal spell and its copies require their locked X")
    expected = bind_announced_x_modal_targets(program.target_schema, x)
    stored = item.context.get("target_schema_override")
    if stored is not None and FrozenMap(stored) != FrozenMap(expected):
        raise ValueError("The persisted modal target schema differs from the original announced X")
    return expected
