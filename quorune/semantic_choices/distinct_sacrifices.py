from __future__ import annotations

"""Seal choices for one simultaneous sacrifice of differently typed permanents."""

from collections import Counter

from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.distinct_sacrifices import (
    DISTINCT_SACRIFICE_FIELD, distinct_sacrifice_effect, distinct_sacrifice_options,
    maximum_sacrifices, role_masks,
)
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from ..zone_trigger_events import ZoneTransitionKind
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


_FIELDS = frozenset({"op", "player", DISTINCT_SACRIFICE_FIELD})
_PROGRESS = frozenset({"_sacrifice_index", "_sacrifice_selected"})
_OFFER = frozenset({"_sacrifice_options", "_sacrifice_may_skip"})


def _scope(effect, actor):
    try:
        return distinct_sacrifice_effect({key: effect[key] for key in _FIELDS}, actor=actor)
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _progress(effect, roles, candidates):
    index = effect.get("_sacrifice_index", 0)
    selected = effect.get("_sacrifice_selected", ())
    if (type(index) is not int or not 0 <= index <= len(roles)
            or not isinstance(selected, (tuple, list)) or len(selected) > index):
        raise SemanticChoiceError("Distinct sacrifice progress is malformed")
    seen_roles, seen_cards = set(), set()
    for row in selected:
        if not isinstance(row, (list, tuple)) or len(row) != 3 or any(type(value) is not str for value in row):
            raise SemanticChoiceError("A selected sacrifice identity is malformed")
        role, ref, logical = row
        current = candidates.get(ref)
        if (role not in roles[:index] or role in seen_roles or ref in seen_cards
                or current is None or current[0] != logical or role not in current[1]):
            raise SemanticChoiceError("A selected sacrifice no longer satisfies its role")
        seen_roles.add(role)
        seen_cards.add(ref)
    if tuple(row[0] for row in selected) != tuple(role for role in roles[:index] if role in seen_roles):
        raise SemanticChoiceError("Selected sacrifice roles must follow the instruction order")
    return index, tuple(tuple(row) for row in selected), seen_cards


def _current_choices(effect, roles, actor, query):
    candidates = {row.ref: (row.logical_object_id, row.types)
                  for row in query.objects(zones=("battlefield",), controller=actor)
                  if set(row.types).intersection(roles)}
    index, selected, used = _progress(effect, roles, candidates)
    remaining = {ref: row for ref, row in candidates.items() if ref not in used}
    tail = roles[index:]
    total = maximum_sacrifices(Counter(role_masks(candidates, roles).values()), len(roles))
    if not tail:
        refs, may_skip, need = (), True, 0
    else:
        refs, may_skip, need = distinct_sacrifice_options(role_masks(remaining, tail), len(tail))
    if len(selected) + need != total:
        raise SemanticChoiceError("A distinct sacrifice choice left a required role unfilled")
    return index, selected, tuple((ref, remaining[ref][0]) for ref in refs), may_skip


def _next(effect, index, selected):
    return FrozenMap({**{key: effect[key] for key in _FIELDS},
                      "_sacrifice_index": index, "_sacrifice_selected": selected})


def _auto(effect, *, reason, next_effects=(), intents=()):
    return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect), preparation_intents=intents,
        auto_continue=AutoContinue(reason=reason, prepend_effects=next_effects))


def prepare_distinct_sacrifices(effect, context):
    actor = context.actor
    if actor != context.stack_controller or actor not in context.query.active_seats or set(effect) not in (_FIELDS, _FIELDS | _PROGRESS):
        raise SemanticChoiceError("Distinct sacrifices require the active resolving controller")
    roles, before, _ = _scope(effect, actor)
    if before is not None:
        if set(effect) != _FIELDS:
            raise SemanticChoiceError("The preceding discard cannot repeat")
        next_effect = {**dict(effect), DISTINCT_SACRIFICE_FIELD: {**dict(effect[DISTINCT_SACRIFICE_FIELD]), "before": None}}
        return _auto(effect, reason="Resolve the controller discard before the sacrifices.",
                     next_effects=(FrozenMap(before), _next(next_effect, 0, ())))
    index, selected, options, may_skip = _current_choices(effect, roles, actor, context.query)
    if index == len(roles):
        refs = tuple(ref for _, ref, _ in selected)
        intents = (MoveObjectsSimultaneouslyIntent(actor=actor, object_refs=refs,
            expected_zones=("battlefield",), destination="graveyard", reason=context.stack_label,
            controlled_only=True, transition_kind=ZoneTransitionKind.SACRIFICE),) if refs else ()
        return _auto(effect, reason="Sacrifice the selected permanents together.", intents=intents)
    if not options:
        return _auto(effect, reason="The current type has no feasible sacrifice.",
                     next_effects=(_next(effect, index + 1, selected),))
    article = "an" if roles[index] == "artifact" else "a"
    return SemanticChoicePreparation(request=SemanticChoiceRequest(
        prompt=f"Choose {article} {roles[index]} to sacrifice.",
        choice=ObjectChoice(field_name="objects", legal_refs=tuple(ref for ref, _ in options),
            zones=("battlefield",), minimum=0 if may_skip else 1, optional=may_skip, controller_relation="actor"),
        public_context=FrozenMap({"stack": context.stack_ref, "operation": effect["op"]})),
        continuation_effect=FrozenMap({**dict(_next(effect, index, selected)),
            "_sacrifice_options": options, "_sacrifice_may_skip": may_skip}))


def complete_distinct_sacrifices(continuation, response, query):
    effect = continuation.effect
    if set(effect) != _FIELDS | _PROGRESS | _OFFER:
        raise SemanticChoiceError("Distinct sacrifice continuation is malformed")
    actor = effect["player"]
    roles, before, _ = _scope(effect, actor)
    if before is not None or actor not in query.active_seats:
        raise SemanticChoiceError("Distinct sacrifice continuation changed controller or phase")
    index, selected, options, may_skip = _current_choices(effect, roles, actor, query)
    if (index == len(roles) or thaw_value(effect["_sacrifice_options"]) != thaw_value(options)
            or effect["_sacrifice_may_skip"] is not may_skip):
        raise SemanticChoiceError("Distinct sacrifice options changed")
    refs = response.get("objects", ())
    legal = dict(options)
    if (not isinstance(refs, (list, tuple)) or not (0 if may_skip else 1) <= len(refs) <= 1
            or any(type(ref) is not str or ref not in legal for ref in refs)):
        raise SemanticChoiceError("Choose a current sacrifice or an allowed empty role")
    added = ((roles[index], refs[0], legal[refs[0]]),) if refs else ()
    return SemanticChoiceCompletion(prepend_effects=(_next(effect, index + 1, (*selected, *added)),))
