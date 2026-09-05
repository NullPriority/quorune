from __future__ import annotations

"""Typed ordinary Storm trigger discovery and immutable copy specification."""

import copy
from typing import Any, Mapping, Protocol

from .ability_fragments import (
    AbilityFragmentError,
    SpellCastKeywordTriggerKind,
    SpellCastKeywordTriggerSpec,
)
from .model import CardInstance, StackItem
from .semantic_runtime.ability_fragments import fragments_from_descriptors
from .errors import StateInvariantError


STORM_SEMANTIC_KEY = "builtin:storm"


class StormHost(Protocol):
    state: Any
    semantics: Any
    seats: list[str]

    def card_record(self, card: Any) -> Any: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _current_turn_history(self, kind: str) -> tuple[Any, ...]: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _stable_runtime_id(self, kind: str, ref: str) -> str: ...

    def _stack_target_schema(
        self, item: StackItem, program: Any
    ) -> Mapping[str, Any] | None: ...


def _selected_face_id(record: Any, card: CardInstance) -> str:
    if card.active_face:
        return str(card.active_face)
    if record.faces:
        return str(record.faces[0].get("name") or "front")
    return "front"


def compiled_storm_specs(
    host: StormHost,
    card: CardInstance,
) -> tuple[SpellCastKeywordTriggerSpec, ...]:
    """Return every trusted ordinary Storm instance on the cast face."""

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
            and fragment.kind is SpellCastKeywordTriggerKind.STORM
        )
    return tuple(result)


def prior_storm_spell_count(host: StormHost) -> int:
    """Snapshot spells cast before the current cast becomes observable."""

    if host.state.turn_history is not None:
        return len(host._current_turn_history("spell_cast"))
    return sum(
        event.code == "stack.cast"
        and event.turn_sequence == host.state.turn_sequence
        for event in host.state.events
    )


def storm_trigger_items(
    host: StormHost,
    *,
    spell: StackItem,
    card: CardInstance,
    program: Any,
    prior_spell_count: int,
) -> tuple[StackItem, ...]:
    """Create one APNAP-placeable occurrence per typed Storm instance."""

    if type(prior_spell_count) is not int or prior_spell_count < 0:
        raise StateInvariantError("Storm prior-spell count is malformed")
    specs = compiled_storm_specs(host, card)
    if not specs:
        return ()
    target_groups = copy.deepcopy(dict(spell.context.get("target_groups") or {}))
    target_snapshots = copy.deepcopy(
        dict(spell.context.get("target_snapshots") or {})
    )
    template = {
        "label": spell.label,
        "source_spell_ref": spell.ref,
        "controller": spell.controller,
        "card_object_id": spell.card_object_id,
        "semantic_key": spell.semantic_key,
        "targets": copy.deepcopy(spell.targets),
        "modes": copy.deepcopy(spell.modes),
        "x_value": spell.x_value,
        "default_destination": spell.default_destination,
        "referred_object_ids": copy.deepcopy(spell.referred_object_ids),
        "target_groups": target_groups,
        "target_snapshots": target_snapshots,
        "target_schema": copy.deepcopy(
            host._stack_target_schema(spell, program)
        ),
    }
    result = []
    for index, spec in enumerate(specs, start=1):
        ref = host._next_ref("S")
        result.append(
            StackItem(
                stack_id=host._stable_runtime_id("stack", ref),
                ref=ref,
                kind="triggered_ability",
                controller=spell.controller,
                label=f"{spell.label} — Storm",
                source_object_id=card.object_id,
                semantic_key=STORM_SEMANTIC_KEY,
                visibility=list(host.seats),
                context={
                    "copy_count": prior_spell_count,
                    "copy_template": copy.deepcopy(template),
                    "storm_instance": index,
                    "storm_spec": spec.to_dict(),
                },
            )
        )
    return tuple(result)


def validated_storm_trigger(
    item: StackItem,
) -> tuple[int, dict[str, Any]]:
    """Validate a current typed Storm occurrence or its exact legacy shape."""

    if item.semantic_key != STORM_SEMANTIC_KEY:
        raise StateInvariantError("Storm owner received another stack item")
    count = item.context.get("copy_count")
    template = item.context.get("copy_template")
    if type(count) is not int or count < 0:
        raise StateInvariantError("Storm copy count is malformed")
    if not isinstance(template, Mapping):
        raise StateInvariantError("Storm copy template is malformed")

    has_spec = "storm_spec" in item.context
    has_instance = "storm_instance" in item.context
    if has_spec != has_instance:
        raise StateInvariantError("Storm trigger version is malformed")
    if has_spec:
        try:
            spec = SpellCastKeywordTriggerSpec.from_dict(
                item.context.get("storm_spec") or {}
            )
        except (AbilityFragmentError, TypeError, ValueError) as exc:
            raise StateInvariantError(
                "Storm trigger descriptor is malformed"
            ) from exc
        if spec.kind is not SpellCastKeywordTriggerKind.STORM:
            raise StateInvariantError("Storm trigger descriptor changed kind")
        instance = item.context.get("storm_instance")
        if type(instance) is not int or instance <= 0:
            raise StateInvariantError("Storm trigger instance is malformed")
    return count, copy.deepcopy(dict(template))


__all__ = [
    "STORM_SEMANTIC_KEY",
    "compiled_storm_specs",
    "prior_storm_spell_count",
    "storm_trigger_items",
    "validated_storm_trigger",
]
