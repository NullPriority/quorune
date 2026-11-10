from __future__ import annotations

"""Typed CR 702.85 Cascade trigger discovery and resolution coordination."""

from typing import Any, Protocol

from .ability_fragments import (
    SpellCastKeywordTriggerKind,
    SpellCastKeywordTriggerSpec,
    canonical_ability_fragments,
    spell_cast_keyword_trigger_specs,
)
from .cast_timing import type_line_has_card_type
from .errors import GameRuleError, StateInvariantError
from .model import CardInstance, StackItem
from .selection.exile_cast import (
    EXILE_CAST_PRODUCER_CASCADE,
    mana_value_of_cost,
)
from .rules.cast_cascade_grants import announced_cascade_instances
from .semantic_runtime.ability_fragments import fragments_from_descriptors
from .semantic_runtime.battlefield_cascade_grants import CASCADE_GRANT_EVENT


CASCADE_SEMANTIC_KEY = "builtin:cascade"


class CascadeHost(Protocol):
    state: Any
    semantics: Any
    seats: list[str]
    active_seats: list[str]

    def card_record(self, card: Any) -> Any: ...

    def _effective_card_data(self, card: Any) -> dict[str, Any]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _stable_runtime_id(self, kind: str, ref: str) -> str: ...

    def move_card(self, object_id: str, destination: str, **kwargs: Any) -> CardInstance: ...

    def _one_shot_exile_cast_options(self, **kwargs: Any) -> tuple[dict[str, Any], ...]: ...

    def _begin_one_shot_exile_cast_choice(self, **kwargs: Any) -> None: ...

    def _finish_one_shot_exile_cast_resolution(self, **kwargs: Any) -> None: ...


def _selected_face_id(record: Any, card: CardInstance) -> str:
    if card.active_face:
        return str(card.active_face)
    if record.faces:
        return str(record.faces[0].get("name") or "front")
    return "front"


def compiled_cascade_specs(
    host: CascadeHost,
    card: CardInstance,
) -> tuple[SpellCastKeywordTriggerSpec, ...]:
    """Return every trusted printed Cascade instance on the selected spell face."""

    record = host.card_record(card)
    if record is None:
        return ()
    expected_face = _selected_face_id(record, card)
    result: list[SpellCastKeywordTriggerSpec] = []
    for program in host.semantics.programs_for_oracle(
        record.oracle_id,
        active_zone="stack",
    ):
        if not host.semantic_program_is_current_trusted(program):
            continue
        if str(program.provenance.get("face_id") or "") != expected_face:
            continue
        result.extend(
            fragment
            for fragment in fragments_from_descriptors(program.handlers)
            if isinstance(fragment, SpellCastKeywordTriggerSpec)
            and fragment.kind is SpellCastKeywordTriggerKind.CASCADE
        )
    return tuple(result)


def cascade_trigger_items(
    host: CascadeHost,
    *,
    spell: StackItem,
    card: CardInstance,
) -> tuple[StackItem, ...]:
    """Materialize one ordinary trigger occurrence per typed Cascade instance."""

    specs = compiled_cascade_specs(host, card)
    has_grants = bool(announced_cascade_instances(spell.context)
                      or host.semantics.programs_for_event(CASCADE_GRANT_EVENT))
    if not specs and not has_grants:
        return ()
    effective = host._effective_card_data(card)
    if has_grants:
        specs += tuple(fragment for fragment in spell_cast_keyword_trigger_specs(
            canonical_ability_fragments(effective.get("ability_fragments", ()))
        ) if fragment.kind is SpellCastKeywordTriggerKind.CASCADE)
    if not specs:
        return ()
    source_mana_value = mana_value_of_cost(
        str(effective.get("mana_cost") or ""),
        x_value=int(spell.x_value or 0),
    )
    result = []
    for index, spec in enumerate(specs, start=1):
        ref = host._next_ref("S")
        result.append(
            StackItem(
                stack_id=host._stable_runtime_id("stack", ref),
                ref=ref,
                kind="triggered_ability",
                controller=spell.controller,
                label=f"{spell.label} — Cascade",
                source_object_id=card.object_id,
                semantic_key=CASCADE_SEMANTIC_KEY,
                visibility=list(host.seats),
                context={
                    "event": "spell.cast",
                    "source_logical_object_id": card.logical_object_id,
                    "source_zone": "stack",
                    "source_spell_ref": spell.ref,
                    "source_spell_mana_value": source_mana_value,
                    "cascade_instance": index,
                    "cascade_spec": spec.to_dict(),
                },
            )
        )
    return tuple(result)


def _cascade_source_mana_value(item: StackItem) -> float:
    raw = item.context.get("source_spell_mana_value")
    if type(raw) not in {int, float} or raw < 0:
        raise StateInvariantError("Cascade trigger mana value is malformed")
    try:
        spec = SpellCastKeywordTriggerSpec.from_dict(
            item.context.get("cascade_spec") or {}
        )
    except (TypeError, ValueError) as exc:
        raise StateInvariantError("Cascade trigger descriptor is malformed") from exc
    if spec.kind is not SpellCastKeywordTriggerKind.CASCADE:
        raise StateInvariantError("Cascade trigger descriptor changed kind")
    instance = item.context.get("cascade_instance")
    if type(instance) is not int or instance <= 0:
        raise StateInvariantError("Cascade trigger instance is malformed")
    return float(raw)


def begin_cascade_resolution(host: CascadeHost, item: StackItem) -> None:
    """Exile to the first eligible nonland, then cast or random-bottom atomically."""

    if item.semantic_key != CASCADE_SEMANTIC_KEY:
        raise StateInvariantError("Cascade owner received another stack item")
    if item.controller not in host.active_seats:
        host._finish_one_shot_exile_cast_resolution(
            item=item,
            producer=EXILE_CAST_PRODUCER_CASCADE,
            cleanup_cards=(),
            outcome="controller_left_game",
            candidate_ref=None,
        )
        return
    source_mana_value = _cascade_source_mana_value(item)
    library = host.state.players[item.controller].zones["library"]
    exiled: list[CardInstance] = []
    candidate: CardInstance | None = None
    while library:
        card = host.state.cards[library[-1]]
        if card.owner != item.controller or not card.is_card_object:
            raise StateInvariantError(
                "Cascade requires an owned physical card in the controller's library"
            )
        host.move_card(
            card.object_id,
            "exile",
            reason="Cascade",
            reveal_to=host.seats,
            semantic_events=True,
        )
        if card.zone != "exile":
            continue
        exiled.append(card)
        record = host.card_record(card)
        if record is None:
            raise StateInvariantError("Cascade exiled an unregistered card")
        front_type_line = (
            str(record.faces[0].get("type_line") or "")
            if record.faces
            else record.type_line
        )
        if (
            not type_line_has_card_type(front_type_line, "land")
            and float(record.mana_value) < source_mana_value
        ):
            candidate = card
            break
    if candidate is not None and host._one_shot_exile_cast_options(
        actor=item.controller,
        card=candidate,
        maximum_mana_value=source_mana_value,
    ):
        host._begin_one_shot_exile_cast_choice(
            item=item,
            card=candidate,
            cleanup_cards=exiled,
            maximum_mana_value=source_mana_value,
            producer=EXILE_CAST_PRODUCER_CASCADE,
        )
        return
    host._finish_one_shot_exile_cast_resolution(
        item=item,
        producer=EXILE_CAST_PRODUCER_CASCADE,
        cleanup_cards=exiled,
        outcome="cast_unavailable" if candidate is not None else "no_candidate",
        candidate_ref=candidate.ref if candidate is not None else None,
    )


__all__ = [
    "CASCADE_SEMANTIC_KEY",
    "CascadeHost",
    "begin_cascade_resolution",
    "cascade_trigger_items",
    "compiled_cascade_specs",
]
