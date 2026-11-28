from __future__ import annotations

"""Apply identical keep-highest replacements and publish the retained occurrence."""

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..entry_copy import copiable_oracle_id
from ..replacement.immutable import FrozenMap
from .die_roll_occurrence import plan_fixed_die_roll


REPLACEMENT_CAPABILITY = "random.die.controller_extra_ignore_lowest"
TRIGGER_CAPABILITY = "trigger.event.controller_retained_dice"
REPLACEMENT_EVENT = "random.dice.replace"
RETAINED_EVENT = "random.dice.retained"
HANDLER = "replacement.random.controller-extra-ignore-lowest.v1"
TRIGGER_TEMPLATE = "controller-retained-dice-source-counter-v1"
SELECTION_FIELD = "retained_die_tie_selection"
_SELECTION = ContextVar("retained_die_tie_response", default=None)


def replacement_descriptor():
    return {"handler_id": HANDLER, "schema_version": 1, "event": REPLACEMENT_EVENT}


def source_counter_condition():
    return {"field": "player", "op": "eq", "value": "$source.controller"}


def source_counter_effect():
    return {"op": "place_counters", "card": "$source.zone_object", "source": "$source",
            "counter": "+1/+1", "amount": 1}


def retained_dice_runtime_dependencies(program):
    if program.event != RETAINED_EVENT and TRIGGER_CAPABILITY not in program.capability_dependencies:
        return ()
    if (program.event != RETAINED_EVENT or program.active_zone != "battlefield" or program.handlers
            or program.target_schema is not None or program.cost_schema is not None or program.trigger_limit is not None
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
            or FrozenMap(program.event_condition or {}) != FrozenMap(source_counter_condition())
            or len(program.effects) != 1 or FrozenMap(program.effects[0]) != FrozenMap(source_counter_effect())
            or type(program.effects[0]["amount"]) is not int):
        raise ValueError("The retained dice trigger requires its exact controller and source counter")
    return (TRIGGER_CAPABILITY, "counter.producer.fixed_effect", "trigger.placement.apnap")


@dataclass(frozen=True, slots=True)
class DieTieSelection:
    stack: str
    seat: str
    pointer: int
    samples: tuple[int, ...]
    retained_index: int

    @classmethod
    def read(cls, raw):
        if (not isinstance(raw, Mapping)
                or set(raw) != {"schema_version", "stack", "seat", "pointer", "samples", "retained_index"}
                or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
                or type(raw["pointer"]) is not int or raw["pointer"] < 0
                or type(raw["retained_index"]) is not int
                or any(type(raw[key]) is not str or not raw[key] for key in ("stack", "seat"))
                or not isinstance(raw["samples"], (tuple, list))
                or any(type(value) is not int for value in raw["samples"])):
            raise ValueError("Retained die choice has an invalid identity")
        return cls(raw["stack"], raw["seat"], raw["pointer"], tuple(raw["samples"]), raw["retained_index"])

    def to_dict(self):
        return {"schema_version": 1, "stack": self.stack, "seat": self.seat, "pointer": self.pointer,
                "samples": list(self.samples), "retained_index": self.retained_index}


class RetainedDieChoiceRequired(Exception):
    def __init__(self, occurrence, indices):
        self.occurrence = occurrence
        self.indices = indices


def _choice_offer(item, effect, pointer, occurrence, indices):
    option_data = []
    branches = {}
    for index in indices:
        key = f"die:{index + 1}"
        ignored = ", ".join(str(other + 1) for other in range(len(occurrence.samples)) if other != index)
        option_data.append({"id": key, "label": f"Keep die {index + 1} and ignore dice {ignored}"})
        sealed = DieTieSelection(item.ref, occurrence.seat, pointer, occurrence.samples, index)
        branches[key] = [{**effect, SELECTION_FIELD: sealed.to_dict()}]
    sample_text = ", ".join(str(value) for value in occurrence.samples)
    return {"op": "choose_option", "player": occurrence.seat,
            "prompt": f"Dice rolled in order: {sample_text}. Choose which tied die to retain.",
            "options": option_data, "then_by_choice": branches}


def with_retained_die_choice(bind):
    """Suspend a tied numeric instruction, then return to its original frame."""
    @wraps(bind)
    def wrapped(host, item, raw_effect, *, instruction_pointer=0):
        from ..semantic_runtime.context import SemanticNodeError

        selection = None
        if SELECTION_FIELD in raw_effect:
            try:
                selection = DieTieSelection.read(raw_effect[SELECTION_FIELD])
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
            if (selection.stack, selection.seat) != (item.ref, item.controller):
                raise SemanticNodeError("Retained die choice belongs to another resolving instruction")
            instruction_pointer = selection.pointer
            raw_effect = {key: value for key, value in raw_effect.items() if key != SELECTION_FIELD}
        token = _SELECTION.set(selection)
        try:
            return bind(host, item, raw_effect, instruction_pointer=instruction_pointer)
        except RetainedDieChoiceRequired as exc:
            if selection is not None:
                raise SemanticNodeError("The selected tied die could not be retained") from exc
            return _choice_offer(item, raw_effect, instruction_pointer, exc.occurrence, exc.indices)
        finally:
            _SELECTION.reset(token)
    return wrapped


def _active_dice_sources(host, actor):
    from ..semantic_runtime.retained_die_replacement import default_retained_die_replacement_registry
    from ..semantic_runtime.self_entry_counters import program_static_component_is_applicable

    replacements = 0
    observers = []
    registry = default_retained_die_replacement_registry()
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if source.controller != actor or source.zone != "battlefield" or source.phased_out:
            continue
        for program in host.semantics.programs_for_oracle(copiable_oracle_id(source)):
            if program.active_zone != "battlefield" or program.event not in (REPLACEMENT_EVENT, RETAINED_EVENT):
                continue
            if not host.semantic_program_is_current_trusted(program):
                continue
            if program.event == RETAINED_EVENT:
                if source not in observers:
                    observers.append(source)
            elif program_static_component_is_applicable(host, program, source):
                for descriptor in program.handlers:
                    if descriptor.get("handler_id") == HANDLER:
                        registry.validate(descriptor)
                        replacements += 1
    return replacements, tuple(observers)


def plan_replaced_game_die(host, item, *, sides, instruction_pointer):
    if type(instruction_pointer) is not int or instruction_pointer < 0:
        raise ValueError("A game die requires a nonnegative resolving instruction pointer")
    affected = item.controller
    selection = _SELECTION.get()
    extra, _observers = _active_dice_sources(host, affected)
    occurrence = plan_fixed_die_roll(
        sides, seed=host.state.config.seed, instruction_key=f"{item.ref}|{instruction_pointer}",
        seat=affected, extra_rolls=extra, ignore_lowest=extra,
    )
    ties = tuple(index for index, value in enumerate(occurrence.samples) if value == occurrence.single_result)
    if len(ties) > 1 and selection is None:
        raise RetainedDieChoiceRequired(occurrence, ties)
    if selection is not None:
        if (len(ties) < 2 or selection.samples != occurrence.samples or selection.retained_index not in ties
                or (selection.stack, selection.seat, selection.pointer) != (item.ref, affected, instruction_pointer)):
            raise ValueError("The selected die is no longer one of the tied retained results")
        occurrence = replace(occurrence, ignored_indices=tuple(
            index for index in range(len(occurrence.samples)) if index != selection.retained_index
        ))
    return occurrence


def publish_replaced_game_die(host, item, occurrence, *, instruction_pointer):
    affected = item.controller
    if (occurrence.seat, occurrence.instruction_key) != (affected, f"{item.ref}|{instruction_pointer}"):
        raise ValueError("The retained die belongs to a different resolving instruction")
    sides = occurrence.sides
    result = occurrence.single_result
    host._log(affected, "die.roll", f"{affected} rolled {result} on a d{sides}.",
              {"player": affected, "sides": sides, "result": result, "stack": item.ref}, importance=2)
    _extra, observers = _active_dice_sources(host, affected)
    if observers:
        host._dispatch_semantic_event(RETAINED_EVENT, {
            "player": affected, "stack": item.ref, "results": list(occurrence.results),
            "sides": sides, "instruction_pointer": instruction_pointer,
        }, sources=observers)
    return occurrence


def roll_replaced_game_die(host, item, *, sides, instruction_pointer):
    occurrence = plan_replaced_game_die(host, item, sides=sides, instruction_pointer=instruction_pointer)
    return publish_replaced_game_die(host, item, occurrence, instruction_pointer=instruction_pointer)
