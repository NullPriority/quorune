"""Close a target return, its controller's land sacrifice, and resolving copy."""

from collections.abc import Mapping

from ..replacement.immutable import thaw_value
from ..target_controller_reference import TARGET_CONTROLLER_INFORMATION_CAPABILITY, target_controller_reference
from .effect_shape_walkers import nested_effects


LAND_PAYMENT_FIELD = "returned_permanent_land_payment"
RETURN_COPY_CAPABILITY = "stack.copy.targeted_return_land_sacrifice"
RETURN_COPY_MECHANIC = "targeted-return-land-sacrifice-copy"
RETURN_TARGET_FIELD = "returned_permanent_target"
RETURN_COPY_VERSION = 2


def return_copy_payment():
    return {"op": "choose_objects", "player": target_controller_reference(), LAND_PAYMENT_FIELD: 1}


def targeted_return_copy_node_capabilities(*, effects, target_schema, mechanic_ids, **_unused):
    if RETURN_COPY_MECHANIC not in mechanic_ids or len(effects) != 2:
        return ()
    bounce, payment = effects
    if (
        thaw_value(payment) != return_copy_payment() or type(payment.get(LAND_PAYMENT_FIELD)) is not int
        or thaw_value(target_schema) != {"zones": ["battlefield"], "categories": ["permanent"],
                                        "count": 1, "types_none": ["land"]}
        or type(target_schema.get("count")) is not int
    ):
        return ()
    from .node_capability_shapes import targeted_return_to_hand_node_capabilities

    required = targeted_return_to_hand_node_capabilities(
        effects=(bounce,), target_schema=thaw_value(target_schema),
        mechanic_ids={"return-to-owner-hand", "cr-115-targets"},
    )
    return (RETURN_COPY_CAPABILITY, TARGET_CONTROLLER_INFORMATION_CAPABILITY, *required) if required else ()


def targeted_return_copy_runtime_dependencies(program):
    marked = any(LAND_PAYMENT_FIELD in effect for effect in nested_effects((program.effects, program.handlers)))
    if not marked and RETURN_COPY_CAPABILITY not in program.capability_dependencies:
        return ()
    required = targeted_return_copy_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or program.handlers or program.cost_schema is not None or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("The return copy requires its exact spell, land sacrifice, and original target controller")
    return required


def is_closed_targeted_return_copy_program(program):
    try:
        required = targeted_return_copy_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def return_copy_control_is_bound(effect, target, actor):
    ref = effect.get(RETURN_TARGET_FIELD)
    return (isinstance(ref, str) and ref and effect.get("player") == actor
            and target is not None and target.targets == (ref,))


def return_copy_instruction(actor, stack_ref, target_ref):
    from .targeted_discard_spell_copy import RESOLVING_COPY_FIELD

    return {"op": "copy_stack_item", "player": actor, "stack": stack_ref,
            RESOLVING_COPY_FIELD: RETURN_COPY_VERSION, RETURN_TARGET_FIELD: target_ref}
