from __future__ import annotations

"""Runtime normalization for closed modal choices."""

from typing import Any, Mapping, Sequence

from ..replacement.immutable import FrozenMap


COMMANDER_CAST_BOTH_MODES_CAPABILITY = "choice.modal.commander_cast_both"
COMMANDER_CAST_MODE_LIMIT_FIELD = "cast_mode_limit"
COMMANDER_CAST_MODE_LIMIT = FrozenMap({"schema_version": 1, "condition": "controls_commander"})
WIZARD_CAST_TWO_MODES_CAPABILITY = "choice.modal.wizard_cast_two"
WIZARD_CAST_MODE_LIMIT = FrozenMap({"schema_version": 2, "condition": "controls_wizard"})


def commander_cast_mode_limit_matches(schema: Mapping[str, Any]) -> bool:
    """Recognize the fixed one-mode or both-while-controlling-a-commander form."""
    condition = schema.get(COMMANDER_CAST_MODE_LIMIT_FIELD)
    return (
        isinstance(condition, Mapping)
        and type(condition.get("schema_version")) is int
        and FrozenMap(condition) == COMMANDER_CAST_MODE_LIMIT
        and (schema.get("min_modes"), schema.get("max_modes")) == (1, 2)
        and isinstance(schema.get("modes"), Mapping)
        and len(schema["modes"]) == 2
    )


def wizard_cast_mode_limit_matches(schema: Mapping[str, Any]) -> bool:
    condition = schema.get(COMMANDER_CAST_MODE_LIMIT_FIELD)
    return (
        isinstance(condition, Mapping)
        and type(condition.get("schema_version")) is int
        and FrozenMap(condition) == WIZARD_CAST_MODE_LIMIT
        and all(type(schema.get(key)) is int for key in ("mode_count", "min_modes", "max_modes"))
        and (schema["mode_count"], schema["min_modes"], schema["max_modes"]) == (1, 1, 2)
        and isinstance(schema.get("modes"), Mapping)
        and len(schema["modes"]) == 3
    )


def conditional_cast_mode_capability(schema: Mapping[str, Any]) -> str | None:
    if commander_cast_mode_limit_matches(schema):
        return COMMANDER_CAST_BOTH_MODES_CAPABILITY
    if wizard_cast_mode_limit_matches(schema):
        return WIZARD_CAST_TWO_MODES_CAPABILITY
    return None


def controls_wizard(host, seat: str) -> bool:
    from ..object_predicate import ObjectQuerySpec
    from ..object_query import object_matches_query

    query = ObjectQuerySpec(zones=("battlefield",), controller=seat, subtypes_any=("wizard",))
    return any(
        object_matches_query(host._public_object_query_result(card), query)
        for card in host.state.cards.values()
        if card.zone == "battlefield" and card.controller == seat and not card.phased_out
    )


OPTIONAL_COMBAT_TRIGGER_MODAL_CAPABILITY = "choice.modal.optional_combat_trigger"
DISTINCT_TURN_TRIGGER_MODES_CAPABILITY = "choice.modal.distinct_trigger_modes_this_turn"
DISTINCT_TURN_TRIGGER_MODES_FIELD = "distinct_trigger_modes_this_turn_version"
_OTHER_CONTROLLED_CREATURE_ENTRY = FrozenMap({"all": [
    {"field": "controller", "op": "eq", "value": "$source.controller"},
    {"field": "card", "op": "ne", "value": "$source.ref"},
]})
_DISTINCT_TURN_MODES = ("mode_1", "mode_2", "mode_3")
_SELF_COMBAT_PLAYER_CONDITION = FrozenMap({"all": [
    {"field": "target_kind", "op": "eq", "value": "player"},
    {"field": "combat", "op": "truthy", "value": True},
    {"field": "source_logical_object_id", "op": "eq", "value": "$source.logical_object_id"},
]})


def has_optional_trigger_modes(schema):
    return (isinstance(schema, Mapping) and isinstance(schema.get("modes"), Mapping)
            and type(schema.get("min_modes")) is int and schema["min_modes"] == 0)


def optional_combat_trigger_context_matches(*, event, active_zone, event_condition):
    return ((event, active_zone) == ("damage.dealt.self", "battlefield")
            and isinstance(event_condition, Mapping)
            and FrozenMap(event_condition) == _SELF_COMBAT_PLAYER_CONDITION)


def has_distinct_turn_trigger_modes(schema):
    return isinstance(schema, Mapping) and DISTINCT_TURN_TRIGGER_MODES_FIELD in schema


def valid_distinct_turn_trigger_modes(schema):
    return (
        has_distinct_turn_trigger_modes(schema)
        and type(schema[DISTINCT_TURN_TRIGGER_MODES_FIELD]) is int
        and schema[DISTINCT_TURN_TRIGGER_MODES_FIELD] == 1
        and all(type(schema.get(key)) is int and schema[key] == 1
                for key in ("mode_count", "min_modes", "max_modes"))
        and isinstance(schema.get("modes"), Mapping)
        and tuple(schema["modes"]) == _DISTINCT_TURN_MODES
        and all(isinstance(mode, Mapping) and mode.get("groups") in ([], ())
                for mode in schema["modes"].values())
    )


def distinct_turn_trigger_context_matches(*, event, active_zone, event_condition):
    return (
        (event, active_zone) == ("creature.enter", "battlefield")
        and isinstance(event_condition, Mapping)
        and FrozenMap(event_condition) == _OTHER_CONTROLLED_CREATURE_ENTRY
    )


def _distinct_trigger_mode_choices(state, item):
    from ..errors import GameRuleError
    from ..turn_history import current_turn_history_events
    from ..turn_history_model import TRIGGER_MODE_CHOSEN_EVENT

    incarnation = item.context.get("source_logical_object_id")
    if (
        item.kind != "triggered_ability"
        or not item.semantic_key
        or not isinstance(incarnation, str)
        or incarnation.rpartition("@")[0] != item.source_object_id
        or not incarnation.rpartition("@")[2].isascii()
        or not incarnation.rpartition("@")[2].isdecimal()
        or state.turn_history is None
    ):
        raise GameRuleError("Distinct trigger mode selection lacks its original source and turn history")
    used = frozenset(
        event.amount for event in current_turn_history_events(
            state.turn_history, turn_sequence=state.turn_sequence, kind=TRIGGER_MODE_CHOSEN_EVENT
        )
        if event.object_incarnation == incarnation and event.target == item.semantic_key
    )
    return incarnation, tuple(mode for index, mode in enumerate(_DISTINCT_TURN_MODES, 1) if index not in used)


def bind_distinct_turn_trigger_modes(state, item, schema):
    if not has_distinct_turn_trigger_modes(schema) or not item.context.get("trigger_target_selection_pending"):
        return schema
    _, available = _distinct_trigger_mode_choices(state, item)
    return {**schema, "modes": {mode: schema["modes"][mode] for mode in available}}


def commit_distinct_turn_trigger_mode(host, item, schema):
    if not has_distinct_turn_trigger_modes(schema):
        return
    from ..errors import GameRuleError
    from ..turn_history_model import TRIGGER_MODE_CHOSEN_EVENT

    incarnation, available = _distinct_trigger_mode_choices(host.state, item)
    if len(item.modes) != 1 or item.modes[0] not in available:
        raise GameRuleError("Choose exactly one unused mode for this trigger")
    host._record_turn_history(
        TRIGGER_MODE_CHOSEN_EVENT, actor=item.controller, object_incarnation=incarnation,
        target=item.semantic_key, target_kind="ability_program", amount=_DISTINCT_TURN_MODES.index(item.modes[0]) + 1,
    )


def canonical_modes(
    schema: Mapping[str, Any],
    modes: Sequence[str] = (),
    *,
    require_modes: bool = True,
) -> tuple[str, ...]:
    """Validate one modal selection and return it in printed order."""

    selected_modes = tuple(str(mode) for mode in modes)
    mode_definitions = schema.get("modes")
    if not isinstance(mode_definitions, Mapping):
        return selected_modes
    minimum_modes = int(schema.get("min_modes", schema.get("mode_count", 1)))
    maximum_modes = int(schema.get("max_modes", schema.get("mode_count", 1)))
    if require_modes and not (
        minimum_modes <= len(selected_modes) <= maximum_modes
    ):
        raise ValueError(
            f"Action requires between {minimum_modes} and {maximum_modes} mode(s)"
        )
    if len(set(selected_modes)) != len(selected_modes):
        raise ValueError("The same mode cannot be selected twice")
    mode_order = {str(mode): index for index, mode in enumerate(mode_definitions)}
    if any(mode not in mode_order for mode in selected_modes):
        unknown = next(mode for mode in selected_modes if mode not in mode_order)
        raise ValueError(f"Unknown target mode {unknown!r}")
    return tuple(sorted(selected_modes, key=mode_order.__getitem__))


__all__ = ["canonical_modes"]
