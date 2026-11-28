"""Closed instructions for exchanging battlefield creatures with creature cards in graveyards."""

from __future__ import annotations

from collections.abc import Mapping

from ..linked_exile_return_model import LINKED_EXILE_RETURN_OPERATION, ExiledObjectIdentity


GRAVEYARD_EXCHANGE_FIELD = "graveyard_exile_sacrifice_return"
GRAVEYARD_EXCHANGE_CAPABILITY = "resolution.graveyard.exile_sacrifice_return"
GRAVEYARD_EXCHANGE_PHASES = ("exile", "sacrifice", "return")


def graveyard_exchange_effects():
    return tuple({
        "op": LINKED_EXILE_RETURN_OPERATION,
        "phase": phase,
        GRAVEYARD_EXCHANGE_FIELD: 1,
    } for phase in GRAVEYARD_EXCHANGE_PHASES)


def is_exact_graveyard_exchange_sequence(effects):
    expected = graveyard_exchange_effects()
    return len(effects) == len(expected) and all(
        type(effect.get(GRAVEYARD_EXCHANGE_FIELD)) is int and dict(effect) == phase
        for effect, phase in zip(effects, expected)
    )


def validate_graveyard_exchange_phase(effect):
    phase = effect.get("phase")
    required = {"op", "phase", GRAVEYARD_EXCHANGE_FIELD}
    allowed = required | {"_replacement_selections"}
    if phase == "return":
        allowed.add("_aura_target_refs")
    if (
        not required <= set(effect) <= allowed
        or effect["op"] != LINKED_EXILE_RETURN_OPERATION
        or phase not in GRAVEYARD_EXCHANGE_PHASES
        or type(effect[GRAVEYARD_EXCHANGE_FIELD]) is not int
        or effect[GRAVEYARD_EXCHANGE_FIELD] != 1
        or not isinstance(effect.get("_replacement_selections", ()), (list, tuple))
    ):
        raise ValueError("The graveyard exchange requires one closed, ordered phase")
    aura_targets = effect.get("_aura_target_refs", {})
    if not isinstance(aura_targets, Mapping) or any(
        type(object_id) is not str or not object_id or type(ref) is not str or not ref
        for object_id, ref in aura_targets.items()
    ):
        raise ValueError("The graveyard exchange Aura targets are malformed")


def graveyard_exchange_program_dependencies(program):
    if not any(GRAVEYARD_EXCHANGE_FIELD in effect for effect in program.effects):
        return ()
    if (
        program.event != "resolve"
        or program.active_zone != "stack"
        or program.target_schema is not None
        or program.cost_schema is not None
        or not is_exact_graveyard_exchange_sequence(program.effects)
    ):
        raise ValueError("The graveyard exchange requires its complete, untargeted spell program")
    return (GRAVEYARD_EXCHANGE_CAPABILITY,)


def graveyard_exchange_exiled_objects(journal, *, phase):
    if (
        not isinstance(journal, Mapping)
        or set(journal) != {"next_phase", "objects"}
        or journal["next_phase"] != phase
        or not isinstance(journal["objects"], (list, tuple))
    ):
        raise ValueError("A graveyard exchange phase cannot run ahead of its committed prefix")
    objects = tuple(ExiledObjectIdentity.from_dict(value) for value in journal["objects"])
    if len({value.object_id for value in objects}) != len(objects):
        raise ValueError("A graveyard exchange cannot retain an object twice")
    return objects
