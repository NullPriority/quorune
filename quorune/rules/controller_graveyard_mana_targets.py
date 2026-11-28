"""Target legality bound to the current target controller's graveyard."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap


CONTROLLER_GRAVEYARD_MANA_PREDICATE = "mana_value_at_most_target_controller_graveyard"
CONTROLLER_GRAVEYARD_MANA_CAPABILITY = "target.mana_value.current_controller_graveyard"
CONTROLLER_GRAVEYARD_MANA_MECHANIC = "target-controller-graveyard-mana-value"


def controller_graveyard_mana_schema(role):
    if role == "counter_stack_target":
        fields = {"zones": ["stack"], "categories": ["spell"], "source_exclusion": True}
    elif role == "destroy":
        fields = {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"]}
    else:
        raise ValueError("The live graveyard threshold requires a supported target role")
    return {**fields, "count": 1, "predicate": CONTROLLER_GRAVEYARD_MANA_PREDICATE}


def controller_graveyard_mana_target_matches(host, row, *, reserved_source_id=None):
    controller = row.get("controller")
    player = host.state.players.get(controller)
    value = row.get("mana_value")
    if player is None or not player.in_game or type(value) not in {int, float} or value < 0:
        return False
    count = sum(
        card.zone == "graveyard" and card.is_card_object
        for object_id in player.zones["graveyard"]
        if object_id != reserved_source_id and (card := host.state.cards.get(object_id)) is not None
    )
    return value <= count


def controller_graveyard_mana_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (not isinstance(target_schema, Mapping)
            or target_schema.get("predicate") != CONTROLLER_GRAVEYARD_MANA_PREDICATE
            or CONTROLLER_GRAVEYARD_MANA_MECHANIC not in mechanic_ids or len(effects) != 1
            or not isinstance(effects[0], Mapping)):
        return ()
    effect = effects[0]
    role = effect.get("op")
    if role == "counter_stack_target":
        reference, operation = "stack", "stack.counter.effect"
    elif role == "destroy":
        reference, operation = "card", "permanent.destroy.effect"
    else:
        return ()
    if (FrozenMap(effect) != FrozenMap({"op": role, reference: "$target.0"})
            or FrozenMap(target_schema) != FrozenMap(controller_graveyard_mana_schema(role))):
        return ()
    return (CONTROLLER_GRAVEYARD_MANA_CAPABILITY, operation, "target.revalidate_resolution")


def _validated_branch_capabilities(schema, effects, mechanics):
    declared = CONTROLLER_GRAVEYARD_MANA_MECHANIC in mechanics
    guarded = schema.get("predicate") == CONTROLLER_GRAVEYARD_MANA_PREDICATE
    if not (declared or guarded):
        return ()
    required = controller_graveyard_mana_node_capabilities(
        effects=effects, target_schema=schema, mechanic_ids=mechanics,
    )
    if not required:
        raise ValueError("The target must retain its exact creature-destroy or spell-counter threshold")
    return required


def controller_graveyard_mana_program_dependencies(program):
    schema = program.target_schema or {}
    if not isinstance(schema, Mapping):
        if CONTROLLER_GRAVEYARD_MANA_CAPABILITY in program.capability_dependencies:
            raise ValueError("The original live target threshold is missing")
        return ()
    modes = schema.get("modes")
    required = set()
    if isinstance(modes, Mapping):
        for branch in modes.values():
            if not isinstance(branch, Mapping):
                continue
            target = {key: value for key, value in branch.items() if key not in {"mechanics", "effects"}}
            required.update(_validated_branch_capabilities(target, branch.get("effects", ()), branch.get("mechanics", ())))
    else:
        required.update(_validated_branch_capabilities(schema, program.effects, program.coverage))
    if not required and CONTROLLER_GRAVEYARD_MANA_CAPABILITY in program.capability_dependencies:
        raise ValueError("The original live target threshold is missing")
    return tuple(sorted(required))
