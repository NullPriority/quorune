"""Apply a roller's optional life-paid adjustment after dice retention."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..entry_copy import copiable_oracle_id
from ..fixed_token_production import TOKEN_LITERAL_NAME_VERSION_FIELD, fixed_ordinary_token_name
from ..life_state import available_life_for_payment
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import PayLifeIntent
from .die_roll_occurrence import FixedDieRollOccurrence, plan_fixed_die_roll
from .retained_die_roll import plan_replaced_game_die, publish_replaced_game_die


ADJUSTMENT_CAPABILITY = "random.die.pay_life_adjust_one"
TOKEN_CAPABILITY = "trigger.event.controller_final_six"
ADJUSTMENT_EVENT = "random.dice.adjust"
FINAL_SIX_EVENT = "random.dice.final_six"
HANDLER = "random.dice.pay-life-adjust-one.v1"
ADJUSTMENT_FIELD = "life_paid_die_adjustment"
CONTINUATION_CONTEXT = "life_paid_die_adjustment_pending"
TURN_FIELD = "life_paid_die_adjustment_used"
TOKEN_DEPENDENCIES = (TOKEN_CAPABILITY, "token.creation.fixed_definition", "trigger.placement.apnap")
_CHOICE = ContextVar("life_paid_die_adjustment", default=None)


def adjustment_descriptor():
    return {"handler_id": HANDLER, "schema_version": 1, "event": ADJUSTMENT_EVENT}


def final_six_trigger_condition():
    return {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "result", "op": "eq", "value": 6},
    ]}


def final_six_token_effect():
    return {
        "op": "create_token", "controller": "$controller",
        "name": fixed_ordinary_token_name("Zombie Employee"), "quantity": 1,
        "characteristics": {
            "type_line": "Token Creature — Zombie Employee", TOKEN_LITERAL_NAME_VERSION_FIELD: 1,
            "colors": ["B"], "power": "2", "toughness": "2",
        },
    }


def night_shift_runtime_dependencies(program):
    if program.event == ADJUSTMENT_EVENT or ADJUSTMENT_CAPABILITY in program.capability_dependencies:
        if (program.event != ADJUSTMENT_EVENT or program.active_zone != "battlefield"
                or len(program.handlers) != 1
                or FrozenMap(program.handlers[0]) != FrozenMap(adjustment_descriptor())
                or not program.ability_id.startswith("static:") or program.effects
                or program.event_condition is not None or program.target_schema is not None
                or program.cost_schema is not None or program.trigger_limit is not None
                or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage):
            raise ValueError("The dice adjustment requires its original static source and price")
        return (ADJUSTMENT_CAPABILITY,)
    if program.event != FINAL_SIX_EVENT and TOKEN_CAPABILITY not in program.capability_dependencies:
        return ()
    if (program.event != FINAL_SIX_EVENT or program.active_zone != "battlefield"
            or not program.ability_id.startswith("trigger:") or program.handlers
            or program.target_schema is not None or program.cost_schema is not None
            or program.trigger_limit is not None or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
            or FrozenMap(program.event_condition or {}) != FrozenMap(final_six_trigger_condition())
            or len(program.effects) != 1 or type(program.effects[0].get("quantity")) is not int
            or FrozenMap(program.effects[0]) != FrozenMap(final_six_token_effect())):
        raise ValueError("A final-six trigger requires one exact Zombie Employee token instruction")
    return TOKEN_DEPENDENCIES


@dataclass(frozen=True, slots=True)
class PaidDieAdjustment:
    source: str
    index: int
    delta: int

    def __post_init__(self):
        if (type(self.source) is not str or not self.source or type(self.index) is not int or self.index < 0
                or type(self.delta) is not int or self.delta not in {-1, 1}):
            raise ValueError("An applied die adjustment requires one source, retained index, and signed unit")

    def to_dict(self):
        return {"source": self.source, "index": self.index, "delta": self.delta}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, Mapping) or set(data) != {"source", "index", "delta"}:
            raise ValueError("An applied die adjustment has a closed field set")
        return cls(data["source"], data["index"], data["delta"])


@dataclass(frozen=True, slots=True)
class DieAdjustmentSelection:
    stack: str
    pointer: int
    turn: int
    occurrence: FixedDieRollOccurrence
    applied: tuple[PaidDieAdjustment, ...] = ()
    source: str | None = None
    index: int | None = None
    delta: int = 0

    def __post_init__(self):
        if (type(self.stack) is not str or not self.stack
                or type(self.pointer) is not int or self.pointer < 0
                or type(self.turn) is not int or self.turn < 0
                or not isinstance(self.occurrence, FixedDieRollOccurrence)
                or self.occurrence.instruction_key != f"{self.stack}|{self.pointer}"
                or type(self.delta) is not int):
            raise ValueError("Invalid die adjustment instruction identity")
        retained = list(self.occurrence.retained_natural_results)
        seen_sources = set()
        if not isinstance(self.applied, tuple):
            raise ValueError("The die adjustment ledger must be immutable")
        for payment in self.applied:
            if (not isinstance(payment, PaidDieAdjustment) or payment.source in seen_sources
                    or payment.index >= len(retained)):
                raise ValueError("The die adjustment ledger contains an invalid payment")
            seen_sources.add(payment.source)
            retained[payment.index] += payment.delta
        if tuple(retained) != self.occurrence.results:
            raise ValueError("The die adjustment ledger does not produce the stored result")
        if self.source is None:
            if self.delta != 0 or self.index is not None:
                raise ValueError("Declining the adjustment cannot modify a die")
        elif (type(self.source) is not str or not self.source or self.source in seen_sources
              or self.delta not in {-1, 1}
              or type(self.index) is not int or not 0 <= self.index < len(self.occurrence.results)):
            raise ValueError("The adjustment must refer to one retained numerical result")

    def to_dict(self):
        occurrence = self.occurrence
        return {
            "schema_version": 1, "stack": self.stack, "pointer": self.pointer, "turn": self.turn,
            "seat": occurrence.seat, "sides": occurrence.sides, "samples": list(occurrence.samples),
            "ignored_indices": list(occurrence.ignored_indices), "results": list(occurrence.results),
            "applied": [entry.to_dict() for entry in self.applied],
            "source": self.source, "index": self.index, "delta": self.delta,
        }

    @classmethod
    def from_dict(cls, data):
        if (not isinstance(data, Mapping) or set(data) != {
            "schema_version", "stack", "pointer", "turn", "seat", "sides", "samples", "ignored_indices",
            "results", "applied", "source", "index", "delta",
        } or type(data["schema_version"]) is not int or data["schema_version"] != 1
                or any(not isinstance(data[key], (list, tuple)) for key in ("samples", "ignored_indices", "results", "applied"))):
            raise ValueError("The die adjustment continuation has an invalid field set")
        occurrence = FixedDieRollOccurrence(
            data["seat"], f"{data['stack']}|{data['pointer']}", data["sides"],
            tuple(data["samples"]), tuple(data["ignored_indices"]), tuple(data["results"]),
        )
        applied = tuple(PaidDieAdjustment.from_dict(entry) for entry in data["applied"])
        return cls(data["stack"], data["pointer"], data["turn"], occurrence, applied,
                   data["source"], data["index"], data["delta"])


class DieAdjustmentChoiceRequired(Exception):
    def __init__(self, selection, sources):
        self.selection = selection
        self.sources = sources


def _adjustment_offer(selection, sources, original_effect):
    options = [{"id": "decline", "label": "Keep the results"}]
    branches = {"decline": [{**original_effect, ADJUSTMENT_FIELD: selection.to_dict()}]}
    for source in sources:
        for index, value in enumerate(selection.occurrence.results):
            for delta in (-1, 1):
                signed = f"{delta:+d}"
                choice = f"{source.ref}:die:{index + 1}:{signed}"
                options.append({
                    "id": choice,
                    "label": f"{source.printed_name} ({source.ref}): Pay 1 life to change die {index + 1} from {value} to {value + delta}",
                })
                chosen = replace(selection, source=source.logical_object_id, index=index, delta=delta)
                branches[choice] = [{**original_effect, ADJUSTMENT_FIELD: chosen.to_dict()}]
    values = ", ".join(str(value) for value in selection.occurrence.results)
    return {
        "op": "choose_option", "player": selection.occurrence.seat,
        "prompt": f"Retained die results: {values}. You may pay 1 life to change one result by 1.",
        "options": options, "then_by_choice": branches,
    }


def with_night_shift_die_choice(bind):
    """Resume the original numeric instruction from a sealed post-roll choice."""
    @wraps(bind)
    def wrapped(host, item, raw_effect, *, instruction_pointer=0):
        from ..semantic_runtime.context import SemanticNodeError

        selected = None
        if ADJUSTMENT_FIELD in raw_effect:
            try:
                selected = DieAdjustmentSelection.from_dict(raw_effect[ADJUSTMENT_FIELD])
                if ((selected.stack, selected.occurrence.seat, selected.turn)
                        != (item.ref, item.controller, host.state.turn_sequence)):
                    raise ValueError("The die adjustment belongs to another resolving instruction")
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            instruction_pointer = selected.pointer
            raw_effect = {key: value for key, value in raw_effect.items() if key != ADJUSTMENT_FIELD}
        token = _CHOICE.set(selected)
        try:
            return bind(host, item, raw_effect, instruction_pointer=instruction_pointer)
        except DieAdjustmentChoiceRequired as exc:
            return _adjustment_offer(exc.selection, exc.sources, raw_effect)
        finally:
            _CHOICE.reset(token)
    return wrapped


def _available_adjustments(host, player):
    from ..semantic_runtime.self_entry_counters import program_static_component_is_applicable

    if available_life_for_payment(host, player) < 1:
        return ()
    available = []
    turn = host.state.turn_sequence
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if source.controller != player or source.zone != "battlefield" or source.phased_out:
            continue
        used = source.annotations.get(TURN_FIELD)
        if used == {"incarnation": source.logical_object_id, "turn": turn}:
            continue
        for program in host.semantics.programs_for_oracle(copiable_oracle_id(source)):
            if (program.event == ADJUSTMENT_EVENT and program.active_zone == "battlefield"
                    and host.semantic_program_is_current_trusted(program)
                    and program_static_component_is_applicable(host, program, source)
                    and night_shift_runtime_dependencies(program)):
                available.append(source)
                break
    return tuple(sorted(available, key=lambda source: (source.zone_timestamp, source.ref)))


def _finish_roll(host, item, occurrence, pointer):
    item.context.pop(CONTINUATION_CONTEXT, None)
    publish_replaced_game_die(host, item, occurrence, instruction_pointer=pointer)
    if 6 not in occurrence.results:
        return occurrence
    observers = tuple(
        source for source in host._semantic_event_sources(zones={"battlefield"})
        if source.controller == occurrence.seat
        and any(program.event == FINAL_SIX_EVENT and host.semantic_program_is_current_trusted(program)
                for program in host.semantics.programs_for_oracle(copiable_oracle_id(source)))
    )
    if observers:
        for index, result in enumerate(occurrence.results):
            if result == 6:
                host._dispatch_semantic_event(FINAL_SIX_EVENT, {
                    "player": occurrence.seat, "stack": item.ref, "instruction_pointer": pointer,
                    "retained_index": index, "result": result,
                }, sources=observers)
    return occurrence


def _validate_pending_roll(host, item, selection):
    pending = item.context.get(CONTINUATION_CONTEXT)
    expected = replace(selection, source=None, index=None, delta=0)
    if not isinstance(pending, Mapping) or FrozenMap(pending) != FrozenMap(expected.to_dict()):
        raise ValueError("The selected die does not match the sealed retained roll")
    occurrence = selection.occurrence
    natural = plan_fixed_die_roll(
        occurrence.sides, seed=host.state.config.seed, instruction_key=occurrence.instruction_key,
        seat=occurrence.seat, rolls=len(occurrence.samples),
    )
    if natural.samples != occurrence.samples:
        raise ValueError("The sealed natural samples do not match their resolving instruction")


def roll_adjusted_game_die(host, item, *, sides, instruction_pointer):
    """Roll or recover one retained occurrence, then apply optional changes."""
    selected = _CHOICE.get()
    applied = ()
    if selected is None:
        occurrence = plan_replaced_game_die(host, item, sides=sides, instruction_pointer=instruction_pointer)
    else:
        occurrence = selected.occurrence
        if (selected.pointer, occurrence.sides) != (instruction_pointer, sides):
            raise ValueError("The die adjustment changed its resolving instruction or die")
        _validate_pending_roll(host, item, selected)
        if selected.source is None:
            return _finish_roll(host, item, occurrence, instruction_pointer)
        eligible = _available_adjustments(host, occurrence.seat)
        source = next((row for row in eligible if row.logical_object_id == selected.source), None)
        if source is None:
            raise ValueError("The chosen life-paid die adjustment is no longer available")
        host.pay_life_intent(PayLifeIntent(occurrence.seat, occurrence.seat, 1, "night_shift_die_adjustment"))
        source.annotations[TURN_FIELD] = {"incarnation": source.logical_object_id, "turn": selected.turn}
        values = list(occurrence.results)
        values[selected.index] += selected.delta
        occurrence = replace(occurrence, results=tuple(values))
        applied = (*selected.applied, PaidDieAdjustment(selected.source, selected.index, selected.delta))
    sources = _available_adjustments(host, occurrence.seat)
    if sources:
        selection = DieAdjustmentSelection(item.ref, instruction_pointer, host.state.turn_sequence, occurrence, applied)
        item.context[CONTINUATION_CONTEXT] = selection.to_dict()
        raise DieAdjustmentChoiceRequired(selection, sources)
    return _finish_roll(host, item, occurrence, instruction_pointer)
