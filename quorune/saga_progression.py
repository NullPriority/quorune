from __future__ import annotations

"""Typed counter ownership for ordinary Saga entry and turn actions."""

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol, Sequence

from .characteristic_evaluation import type_parts
from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .counter_placement import (
    CounterPlacementCommitPlan,
    CounterPlacementError,
    CounterPlacementRequest,
    commit_counter_placement_plan,
    log_counter_placement_replacements,
    plan_prepared_counter_placement_commit,
    prepare_counter_placements,
)
from .model import StackItem
from .saga_lifecycle import SagaFinalChapterSnapshot
from .trigger_processing import enqueue_trigger_batch
from .trigger_discovery import program_has_current_ability_fragments


_CHAPTER_EVENT = re.compile(r"saga\.chapter\.(?P<number>[1-9]\d*)")


class SagaProgressionError(ValueError):
    """A represented Saga counter action cannot be completed exactly."""


class SagaProgressionHost(Protocol):
    state: Any
    semantics: Any

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _dispatch_semantic_event(
        self,
        event: str,
        context: Mapping[str, Any],
        *,
        sources: Sequence[Any] | None = None,
        trigger_batch: list[StackItem] | None = None,
    ) -> list[str]: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class SagaLoreSubject:
    object_id: str
    object_ref: str
    logical_object_id: str
    controller: str
    before: int
    chapters: tuple[int, ...]

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value or value != value.strip()
            for value in (
                self.object_id,
                self.object_ref,
                self.logical_object_id,
                self.controller,
            )
        ):
            raise SagaProgressionError(
                "Saga lore subjects require canonical nonempty identity"
            )
        if type(self.before) is not int or self.before < 0:
            raise SagaProgressionError(
                "Saga lore subjects require nonnegative prior lore"
            )
        if (
            not self.chapters
            or any(type(value) is not int or value < 1 for value in self.chapters)
            or tuple(sorted(set(self.chapters))) != self.chapters
        ):
            raise SagaProgressionError(
                "Saga lore subjects require canonical chapter declarations"
            )


@dataclass(frozen=True, slots=True)
class SagaLoreTurnAction:
    controller: str
    subjects: tuple[SagaLoreSubject, ...]
    counter_plan: CounterPlacementCommitPlan

    def __post_init__(self) -> None:
        if type(self.controller) is not str or not self.controller:
            raise SagaProgressionError(
                "Saga turn actions require a controller"
            )
        if not all(isinstance(value, SagaLoreSubject) for value in self.subjects):
            raise SagaProgressionError(
                "Saga turn actions require typed subjects"
            )
        if not isinstance(self.counter_plan, CounterPlacementCommitPlan):
            raise SagaProgressionError(
                "Saga turn actions require a typed counter-placement plan"
            )
        if len(self.subjects) != len(self.counter_plan.rows):
            raise SagaProgressionError(
                "Saga turn action subjects and transitions must align"
            )


def _is_current_saga(host: SagaProgressionHost, card: Any) -> bool:
    if card.zone != "battlefield" or card.phased_out:
        return False
    _types, subtypes, _supertypes = type_parts(
        str(host._effective_card_data(card).get("type_line") or "")
    )
    return "saga" in subtypes


def represented_chapter_numbers(
    host: SagaProgressionHost,
    card: Any,
) -> tuple[int, ...]:
    """Return only trusted typed chapter-event declarations for this face."""

    numbers: set[int] = set()
    characteristics: Mapping[str, Any] | None = None
    for program in host.semantics.programs_for_oracle(card.oracle_id):
        match = _CHAPTER_EVENT.fullmatch(str(program.event or ""))
        if match is None or not host.semantic_program_is_current_trusted(program):
            continue
        if CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage:
            if characteristics is None:
                characteristics = host._effective_card_data(card)
            if not program_has_current_ability_fragments(
                program,
                characteristics,
            ):
                continue
        numbers.add(int(match.group("number")))
    return tuple(sorted(numbers))


def _trusted_chapter_numbers(
    host: SagaProgressionHost,
    card: Any,
) -> tuple[int, ...]:
    numbers = {
        int(match.group("number"))
        for program in host.semantics.programs_for_oracle(card.oracle_id)
        if (match := _CHAPTER_EVENT.fullmatch(str(program.event or "")))
        is not None
        and host.semantic_program_is_current_trusted(program)
    }
    return tuple(sorted(numbers))


def _chapter_item_matches_incarnation(
    host: SagaProgressionHost,
    card: Any,
    item: StackItem | Mapping[str, Any],
) -> bool:
    if isinstance(item, StackItem):
        source_object_id = item.source_object_id
        semantic_key = item.semantic_key
        context = item.context
    elif isinstance(item, Mapping):
        source_object_id = item.get("source_object_id")
        semantic_key = item.get("semantic_key")
        context = item.get("context")
    else:
        raise SagaProgressionError(
            "Saga pending chapter entries must be stack items"
        )
    if not isinstance(context, Mapping):
        raise SagaProgressionError(
            "Saga pending chapter context must be an object"
        )
    if source_object_id != card.object_id:
        return False
    if context.get("source_logical_object_id") != card.logical_object_id:
        return False
    program = host.semantics.get(str(semantic_key or ""))
    return bool(
        program is not None
        and _CHAPTER_EVENT.fullmatch(str(program.event or "")) is not None
        and host.semantic_program_is_current_trusted(program)
    )


def saga_chapter_trigger_pending(
    host: SagaProgressionHost,
    card: Any,
) -> bool:
    """Return whether this exact Saga incarnation has a chapter pending."""

    if any(
        _chapter_item_matches_incarnation(host, card, item)
        for item in host.state.stack
    ):
        return True
    for batch in host.state.pending_trigger_batches:
        if not isinstance(batch, Mapping):
            raise SagaProgressionError(
                "Saga pending trigger batches must be objects"
            )
        groups = batch.get("groups", ())
        if not isinstance(groups, (list, tuple)):
            raise SagaProgressionError(
                "Saga pending trigger groups must be an array"
            )
        for group in groups:
            if not isinstance(group, Mapping):
                raise SagaProgressionError(
                    "Saga pending trigger groups must be objects"
                )
            items = group.get("items", ())
            if not isinstance(items, (list, tuple)):
                raise SagaProgressionError(
                    "Saga pending trigger items must be an array"
                )
            if any(
                _chapter_item_matches_incarnation(host, card, item)
                for item in items
            ):
                return True
    return False


def saga_final_chapter_snapshot(
    host: SagaProgressionHost,
    card: Any,
) -> SagaFinalChapterSnapshot | None:
    """Capture the represented final-chapter state of one current Saga."""

    if not _is_current_saga(host, card):
        return None
    trusted_chapters = _trusted_chapter_numbers(host, card)
    if not trusted_chapters:
        raise SagaProgressionError(
            "Saga final-chapter state requires trusted typed chapter programs"
        )
    chapters = represented_chapter_numbers(host, card)
    if not chapters:
        return None
    lore = card.counters.get("lore", 0)
    if type(lore) is not int or lore < 0:
        raise SagaProgressionError(
            "Saga lore counters must be a nonnegative integer"
        )
    return SagaFinalChapterSnapshot(
        object_id=card.object_id,
        logical_object_id=card.logical_object_id,
        controller=card.controller,
        lore_counters=lore,
        chapter_numbers=chapters,
        chapter_trigger_pending=saga_chapter_trigger_pending(host, card),
    )


def capture_saga_lore_turn_action(
    host: SagaProgressionHost,
    controller: str,
    *,
    replacement_selections: Sequence[
        str | None | Mapping[str, Any]
    ] = (),
    replacement_event_ids: Sequence[str] | None = None,
) -> SagaLoreTurnAction:
    """Snapshot one simultaneous CR 714.3c turn-based counter action."""

    player = host.state.players.get(controller)
    if player is None:
        raise SagaProgressionError("Saga turn-action controller is not active")
    subjects: list[SagaLoreSubject] = []
    requests: list[CounterPlacementRequest] = []
    for object_id in tuple(player.zones["battlefield"]):
        card = host.state.cards.get(object_id)
        if card is None or card.controller != controller:
            continue
        if not _is_current_saga(host, card):
            continue
        chapters = _trusted_chapter_numbers(host, card)
        if not chapters:
            raise SagaProgressionError(
                "Saga progression requires trusted typed chapter programs"
            )
        before = int(card.counters.get("lore", 0))
        if before < 0:
            raise SagaProgressionError("Saga lore counters cannot be negative")
        subjects.append(
            SagaLoreSubject(
                object_id=card.object_id,
                object_ref=card.ref,
                logical_object_id=card.logical_object_id,
                controller=card.controller,
                before=before,
                chapters=chapters,
            )
        )
        requests.append(
            CounterPlacementRequest(
                subject_kind="permanent",
                subject_id=card.object_id,
                counter_name="lore",
                amount=1,
                placing_player=controller,
                effect_generated=False,
            )
        )
    try:
        prepared = prepare_counter_placements(
            host,
            tuple(requests),
            selections=replacement_selections,
            event_ids=replacement_event_ids,
        )
        plan = plan_prepared_counter_placement_commit(host, prepared)
    except CounterPlacementError as exc:
        raise SagaProgressionError(str(exc)) from exc
    return SagaLoreTurnAction(
        controller=controller,
        subjects=tuple(subjects),
        counter_plan=plan,
    )


def dispatch_saga_chapters(
    host: SagaProgressionHost,
    card: Any,
    *,
    previous_lore: int,
    trigger_batch: list[StackItem],
) -> None:
    """Dispatch every represented chapter threshold crossed by one event."""

    if type(previous_lore) is not int or previous_lore < 0:
        raise SagaProgressionError("Previous Saga lore must be nonnegative")
    if not _is_current_saga(host, card):
        return
    current_lore = int(card.counters.get("lore", 0))
    if current_lore < previous_lore:
        raise SagaProgressionError(
            "Saga chapter dispatch cannot consume a counter-removal event"
        )
    if not _trusted_chapter_numbers(host, card):
        raise SagaProgressionError(
            "Saga chapter dispatch requires trusted typed chapter programs"
        )
    represented = set(represented_chapter_numbers(host, card))
    if not represented:
        return
    for chapter in range(previous_lore + 1, current_lore + 1):
        if chapter not in represented:
            continue
        host._dispatch_semantic_event(
            f"saga.chapter.{chapter}",
            {
                "card": card.ref,
                "controller": card.controller,
                "chapter": chapter,
            },
            sources=(card,),
            trigger_batch=trigger_batch,
        )


def dispatch_saga_entry_chapters(
    host: SagaProgressionHost,
    card: Any,
    *,
    read_ahead_chapter: int | None = None,
    trigger_batch: list[StackItem],
) -> None:
    """Dispatch chapters crossed by the completed as-enters event."""

    if read_ahead_chapter is not None:
        if type(read_ahead_chapter) is not int or read_ahead_chapter < 1:
            raise SagaProgressionError(
                "Read Ahead entry chapter must be a positive integer"
            )
        if not _is_current_saga(host, card):
            return
        if not _trusted_chapter_numbers(host, card):
            raise SagaProgressionError(
                "Saga chapter dispatch requires trusted typed chapter programs"
            )
        represented = set(represented_chapter_numbers(host, card))
        if not represented:
            return
        current_lore = int(card.counters.get("lore", 0))
        # CR 702.155a permits an entry-turn chapter only when the Saga has
        # exactly that chapter's number after all counter replacements.
        if current_lore in represented:
            host._dispatch_semantic_event(
                f"saga.chapter.{current_lore}",
                {
                    "card": card.ref,
                    "controller": card.controller,
                    "chapter": current_lore,
                },
                sources=(card,),
                trigger_batch=trigger_batch,
            )
        return
    dispatch_saga_chapters(
        host,
        card,
        previous_lore=0,
        trigger_batch=trigger_batch,
    )


def commit_saga_lore_turn_action(
    host: SagaProgressionHost,
    action: SagaLoreTurnAction,
    *,
    trigger_batch: list[StackItem] | None = None,
) -> tuple[str, ...]:
    """Commit all lore counters before producing any chapter trigger."""

    if not isinstance(action, SagaLoreTurnAction):
        raise SagaProgressionError("Saga progression requires a typed action")
    for subject in action.subjects:
        card = host.state.cards.get(subject.object_id)
        if (
            card is None
            or card.ref != subject.object_ref
            or card.logical_object_id != subject.logical_object_id
            or card.controller != subject.controller
            or not _is_current_saga(host, card)
            or _trusted_chapter_numbers(host, card) != subject.chapters
        ):
            raise SagaProgressionError(
                "Saga turn-action snapshot changed before commit"
            )
    try:
        results = commit_counter_placement_plan(
            host,
            action.counter_plan,
            reason="Saga lore turn action",
            log=False,
        )
    except CounterPlacementError as exc:
        raise SagaProgressionError(str(exc)) from exc
    owns_trigger_batch = trigger_batch is None
    pending = trigger_batch if trigger_batch is not None else []
    changed: list[str] = []
    log_counter_placement_replacements(host, action.counter_plan.prepared)
    for subject, result in zip(
        action.subjects,
        results,
        strict=True,
    ):
        card = host.state.cards[subject.object_id]
        if result.after != result.before:
            changed.append(card.object_id)
        host._log(
            action.controller,
            "saga.lore",
            f"{card.ref} received lore counter {result.after}.",
            {
                "source": card.ref,
                "before": result.before,
                "chapter": result.after,
                "rule": "714.3c",
                **(
                    {"placed": result.placed}
                    if result.placed != 1
                    else {}
                ),
            },
            importance=1,
            changed_objects=[card.object_id],
            changed_players=[action.controller],
        )
        dispatch_saga_chapters(
            host,
            card,
            previous_lore=subject.before,
            trigger_batch=pending,
        )
    if owns_trigger_batch:
        enqueue_trigger_batch(host, pending)
    return tuple(changed)


def advance_active_player_sagas(
    host: SagaProgressionHost,
    controller: str,
    *,
    trigger_batch: list[StackItem] | None = None,
    replacement_selections: Sequence[
        str | None | Mapping[str, Any]
    ] = (),
    replacement_event_ids: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Capture and commit the represented ordinary precombat Saga action."""

    return commit_saga_lore_turn_action(
        host,
        capture_saga_lore_turn_action(
            host,
            controller,
            replacement_selections=replacement_selections,
            replacement_event_ids=replacement_event_ids,
        ),
        trigger_batch=trigger_batch,
    )


def saga_step_batch(
    host: SagaProgressionHost,
    controller: str,
    phase: str,
    step: str,
    held_triggers: Sequence[StackItem],
    *,
    replacement_selections: Sequence[
        str | None | Mapping[str, Any]
    ] = (),
    replacement_event_ids: Sequence[str] | None = None,
) -> list[StackItem]:
    """Combine a Saga turn action with triggers already waiting for priority."""

    pending = list(held_triggers)
    if phase == "precombat_main" and step == "main":
        advance_active_player_sagas(
            host,
            controller,
            trigger_batch=pending,
            replacement_selections=replacement_selections,
            replacement_event_ids=replacement_event_ids,
        )
    return pending


__all__ = [
    "SagaLoreSubject",
    "SagaLoreTurnAction",
    "SagaProgressionError",
    "advance_active_player_sagas",
    "capture_saga_lore_turn_action",
    "commit_saga_lore_turn_action",
    "dispatch_saga_chapters",
    "dispatch_saga_entry_chapters",
    "represented_chapter_numbers",
    "saga_chapter_trigger_pending",
    "saga_final_chapter_snapshot",
    "saga_step_batch",
]
