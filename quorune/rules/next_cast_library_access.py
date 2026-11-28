from __future__ import annotations

"""Authorize one same-turn library result from an immediate mana ability."""

from typing import Mapping

from ..errors import GameRuleError
from ..next_cast_library_model import (
    NEXT_CAST_LIBRARY_ACCESS,
    NEXT_CAST_LIBRARY_MANA_CAPABILITY,
    NEXT_CAST_LIBRARY_MANA_RESULT_VERSION,
    NEXT_CAST_LIBRARY_PROGRESS_VERSION,
    NEXT_CAST_LIBRARY_SCAN_VERSION,
    printed_next_cast_library_mana,
    valid_exclusive_mana_value,
    validate_next_cast_library_trigger,
)


def arm_next_cast_library_access(host, actor, source) -> None:
    from ..trigger_processing import schedule_delayed_trigger

    if (
        source is None or host.state.cards.get(source.object_id) is not source
        or source.zone != "battlefield" or source.phased_out or source.controller != actor
    ):
        raise GameRuleError("Next-cast library access requires the paid mana source")
    schedule_delayed_trigger(
        host,
        controller=actor,
        label="Exile through a qualifying card after your next cast this turn",
        event_kind="spell.cast",
        condition={NEXT_CAST_LIBRARY_ACCESS: {"schema_version": 1}},
        stack_template={},
        source_object_id=source.object_id,
        source_logical_object_id=source.logical_object_id,
        once=True,
        expires_turn_sequence=host.state.turn_sequence,
    )


def next_cast_library_matches(trigger, context) -> bool:
    from .spell_cast_events import SpellCastEvent

    validate_next_cast_library_trigger(trigger)
    return SpellCastEvent.from_context(context).controller == trigger.controller


def bind_next_cast_library_occurrence(host, trigger, item, context) -> None:
    from ..discover_model import QUALIFIED_SCAN_OPERATION
    from .spell_cast_events import SpellCastEvent

    validate_next_cast_library_trigger(trigger)
    cast = SpellCastEvent.from_context(context)
    spell = next((candidate for candidate in host.state.stack if candidate.ref == cast.stack_ref), None)
    if (
        cast.controller != trigger.controller or not valid_exclusive_mana_value(cast.mana_value)
        or spell is None or spell.kind != "spell" or spell.controller != cast.controller
        or spell.card_object_id != cast.object_id
    ):
        raise ValueError("Next-cast library access lost its committed spell occurrence")
    item.context[NEXT_CAST_LIBRARY_ACCESS] = {
        "schema_version": 1,
        "caster": cast.controller,
        "spell_ref": cast.stack_ref,
        "spell_logical_object_id": cast.logical_object_id,
        "exclusive_mana_value": cast.mana_value,
    }
    item.context["dynamic_effects"] = [{
        "op": QUALIFIED_SCAN_OPERATION,
        "schema_version": NEXT_CAST_LIBRARY_SCAN_VERSION,
        "player": cast.controller,
        "supplier": cast.controller,
        "exclusive_mana_value": cast.mana_value,
    }]


def require_next_cast_library_resolution(item, actor: str, exclusive_mana_value: int | float) -> None:
    occurrence = item.context.get(NEXT_CAST_LIBRARY_ACCESS) if item is not None else None
    if (
        item is None or item.kind != "triggered_ability" or item.controller != actor
        or not isinstance(occurrence, Mapping)
        or set(occurrence) != {
            "schema_version", "caster", "spell_ref", "spell_logical_object_id", "exclusive_mana_value",
        }
        or type(occurrence["schema_version"]) is not int or occurrence["schema_version"] != 1
        or occurrence["caster"] != actor
        or any(type(occurrence[field]) is not str or not occurrence[field]
               for field in ("spell_ref", "spell_logical_object_id"))
        or not valid_exclusive_mana_value(occurrence["exclusive_mana_value"])
        or occurrence["exclusive_mana_value"] != exclusive_mana_value
    ):
        raise GameRuleError("Next-cast library resolution lost its sealed caster or mana value")
