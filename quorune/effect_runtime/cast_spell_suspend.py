"""Exile only the retained live original through the canonical zone owner."""

from collections.abc import Mapping

from ..continuous_effect_state import ResolutionEffectSource
from ..counter_placement import (
    CounterPlacementError, CounterPlacementRequest, commit_prepared_counter_placements, prepare_counter_placements,
)
from ..errors import GameRuleError
from ..event_spell_copy_model import EVENT_SPELL_COPY_CONTEXT
from ..replacement.immutable import FrozenMap
from ..rules.cast_spell_suspend import FIELD, TIME_COUNTERS, is_closed_cast_spell_suspend_program
from ..rules.event_spell_copy import decode_event_spell_copy_source
from ..suspend import record_cast_suspension
from ..zone_trigger_events import ZoneTransitionKind
from .linked_zone_movement import commit_resolved_zone_movement


def _resolved_cast_spell(host, effect, actor):
    public = set(effect) - {"reason", "_runtime_source", "_replacement_selections"}
    if (public != {"op", FIELD, "stack", "copy_source"} or effect["op"] != "exile"
            or type(effect[FIELD]) is not int or effect[FIELD] != 1):
        raise GameRuleError("Cast suspension requires its closed original-spell instruction")
    source = ResolutionEffectSource.from_effect(effect)
    resolving = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    if (resolving is None or resolving.kind not in {"triggered_ability", "triggered_ability_copy"}
            or resolving.controller != actor or resolving.context.get("currently_resolving") is not True
            or not isinstance(effect["copy_source"], Mapping)
            or not isinstance(resolving.context.get(EVENT_SPELL_COPY_CONTEXT), Mapping)
            or FrozenMap(effect["copy_source"]) != FrozenMap(resolving.context[EVENT_SPELL_COPY_CONTEXT])):
        raise GameRuleError("Cast suspension lost its original resolving trigger and cast snapshot")
    program = host.semantics.get(resolving.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_cast_spell_suspend_program(program)):
        raise GameRuleError("Cast suspension has no current trusted copy-and-exile program")
    bound = decode_event_spell_copy_source(effect["copy_source"])
    original = bound.item()
    if effect["stack"] != original.ref:
        raise GameRuleError("Cast suspension retained a different stack spell")
    live = next((item for item in host.state.stack if item.ref == original.ref), None)
    if live is None:
        return source, None
    card = host.state.cards.get(live.card_object_id)
    if (live.stack_id != original.stack_id or live.card_object_id != original.card_object_id
            or live.kind not in {"spell", "spell_copy"} or card is None or card.zone != "stack"
            or card.logical_object_id != bound.logical_object_id):
        raise GameRuleError("Cast suspension requires the original physical stack incarnation")
    return source, card


def _separate_replacement_selections(effect, counter_event_id):
    selections = effect.get("_replacement_selections", ())
    if not isinstance(selections, (list, tuple)):
        raise GameRuleError("Cast suspension replacement selections must be a sequence")
    counter = tuple(choice for choice in selections
                    if isinstance(choice, Mapping) and choice.get("event_id") == counter_event_id)
    move = tuple(choice for choice in selections
                 if not isinstance(choice, Mapping) or choice.get("event_id") != counter_event_id)
    return move, counter


def apply_cast_spell_suspend(host, effect, *, actor, reason):
    try:
        source, card = _resolved_cast_spell(host, effect, actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    if card is None:
        return None
    is_physical = card.object_kind == "card"
    counter_event_id = f"counter.place:{source.stack_ref}:{card.ref}:cast-suspend"
    move_selections, counter_selections = _separate_replacement_selections(effect, counter_event_id)
    with host.transaction():
        moved = commit_resolved_zone_movement(
            host, card, {"_replacement_selections": move_selections}, destination="exile",
            reason=reason, transition_kind=ZoneTransitionKind.ORDINARY,
        )
        if not is_physical or card.zone != "exile":
            if counter_selections:
                raise GameRuleError("Counter replacement has no original card in exile")
            return moved
        try:
            counters = prepare_counter_placements(
                host, (CounterPlacementRequest(
                    subject_kind="permanent", subject_id=card.object_id, counter_name="time",
                    amount=TIME_COUNTERS, placing_player=actor, source_ref=card.ref, effect_generated=True,
                ),), selections=counter_selections, event_ids=(counter_event_id,),
            )
            commit_prepared_counter_placements(host, counters, reason=reason)
        except CounterPlacementError as exc:
            raise GameRuleError(str(exc)) from exc
        record_cast_suspension(host, card)
    return moved
