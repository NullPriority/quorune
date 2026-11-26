from __future__ import annotations

"""Hold a private search result until the canonical joint entry commits."""

from collections.abc import Mapping, Sequence

from ..entry_counters import capture_prospective_entry_characteristics
from ..errors import GameRuleError
from ..external_entry_state_model import PERMANENT_CARD_TYPES
from ..exiled_target_library_search import (
    EXILED_TARGET_SEARCH_BINDING,
    EXILED_TARGET_RETURN_SPEC,
    basic_land_search_with_exiled_target,
    validate_basic_land_search_with_exiled_target,
)
from ..zone_transitions import ZoneTransitionOwner
from .exiled_target_search_shapes import exiled_target_search_runtime_dependencies
from .library_selection import LibrarySelectionObjectIdentity

_SELECTION = "linked_basic_land_search_selection"


def require_combined_entry_program(host, item):
    program = host.semantics.get(item.semantic_key)
    if (
        item.kind != "triggered_ability" or item.context.get("currently_resolving") is not True
        or program is None or not host.semantic_program_is_current_trusted(program)
    ):
        raise GameRuleError("Combined entry requires its current trusted resolving trigger")
    try:
        dependencies = exiled_target_search_runtime_dependencies(program)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    if not dependencies or not set(dependencies) <= set(program.capability_dependencies):
        raise GameRuleError("Combined entry has no complete trusted program")


def _completed_exile(item):
    journal = item.context.get("linked_exile_returns")
    exile = journal.get(EXILED_TARGET_SEARCH_BINDING) if isinstance(journal, Mapping) else None
    if (
        not isinstance(exile, Mapping) or set(exile) != {"spec", "objects", "completed"}
        or exile["spec"] != EXILED_TARGET_RETURN_SPEC.to_dict() or exile["completed"] is not False
    ):
        raise GameRuleError("Combined entry lost the original exile result")
    return exile


def _land_identity(card):
    return LibrarySelectionObjectIdentity(
        object_id=card.object_id, logical_object_id=card.logical_object_id, ref=card.ref,
    )


def stage_combined_entry_selection(host, item, effect, selected: Sequence[str], *, prohibited=False):
    """Save the legal private selection without moving it or shuffling yet."""

    validate_basic_land_search_with_exiled_target(effect)
    require_combined_entry_program(host, item)
    _completed_exile(item)
    if _SELECTION in item.context or len(selected) > 1 or (selected and prohibited):
        raise GameRuleError("Combined entry search was already selected or has invalid cardinality")
    land = None
    if selected:
        card = host._resolve_object(item.controller, selected[0], zones={"library"}, owned_only=True)
        if not host._search_candidate_matches(card, effect["selector"]):
            raise GameRuleError("Combined entry selection no longer matches the basic-land search")
        land = _land_identity(card).to_dict()
    item.context[_SELECTION] = {"land": land, "prohibited": prohibited}


def _selected_land(host, item, selection):
    if not isinstance(selection, Mapping) or set(selection) != {"land", "prohibited"}:
        raise GameRuleError("Combined entry lacks its private search completion")
    if type(selection["prohibited"]) is not bool or (selection["prohibited"] and selection["land"] is not None):
        raise GameRuleError("Combined entry search completion is malformed")
    if selection["land"] is None:
        return None
    try:
        retained = LibrarySelectionObjectIdentity.from_dict(selection["land"])
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    land = host.state.cards.get(retained.object_id)
    if (
        land is None or land.zone != "library" or land.owner != item.controller or _land_identity(land) != retained
        or not host._search_candidate_matches(land, basic_land_search_with_exiled_target()["selector"])
    ):
        raise GameRuleError("Combined entry lost the selected basic-land occurrence")
    return land


def _can_enter_battlefield(host, card):
    _, type_line = capture_prospective_entry_characteristics(host, card=card, enter_face=None)
    return bool(host._type_parts(type_line)[0] & PERMANENT_CARD_TYPES)


def return_combined_entry(host, item, effect, exiled, *, reason):
    selection = item.context.get(_SELECTION)
    land = _selected_land(host, item, selection)
    returnable = tuple(card for card in exiled if _can_enter_battlefield(host, card))
    cards = returnable if land is None else (*returnable, land)
    actor = item.controller
    if cards:
        ZoneTransitionOwner(host).move_cards_simultaneously(
            tuple((card.object_id, "battlefield") for card in cards),
            reason=reason,
            destination_controllers={card.object_id: actor for card in cards},
            putting_players={card.object_id: actor for card in cards},
            replacement_selections=effect.get("_replacement_selections", ()),
            aura_target_refs=effect.get("_aura_target_refs"),
        )
    if not selection["prohibited"]:
        public_land = land is not None and land.zone in {"battlefield", "graveyard", "exile", "command"} and not land.face_down
        host._log(
            actor, "library.search", f"{actor} searched their library.",
            {"source": item.ref, "destination": "battlefield", "count": int(land is not None), "revealed": False,
             "objects": [land.ref] if public_land else []},
            importance=2, changed_objects=[land.object_id] if public_land else [], changed_players=[actor],
        )
    item.context.pop(_SELECTION)
    return [card.ref for card in cards if card.zone == "battlefield"]
